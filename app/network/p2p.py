"""Peer-to-peer networking module for local collaboration."""

from __future__ import annotations

import socket
import json
import hashlib
import hmac
import ipaddress
from dataclasses import dataclass
from typing import Callable
from app.security import SecurityError

@dataclass
class PeerInfo:
    """Dataclass holding information about a peer."""
    address: str
    port: int
    peer_id: str
    display_name: str

def is_private_ip(ip: str) -> bool:
    """Check if an IP address is in a private or loopback range, rejecting wildcards."""
    try:
        ip_clean = ip.split('%')[0]
        ip_obj = ipaddress.ip_address(ip_clean)
        if ip_obj.is_multicast or ip_obj.is_unspecified:
            return False
        if ip_clean in {"0.0.0.0", "::", "255.255.255.255"}:
            return False
        return ip_obj.is_private or ip_obj.is_loopback
    except ValueError:
        return False

def validate_bind_address(address: str) -> None:
    """Validate that the address is safe to bind or connect to, preventing traversal/public routing."""
    if address in {"0.0.0.0", "::", "255.255.255.255"}:
        raise SecurityError("Wildcard or broadcast address binds/connections are prohibited.")
    if not is_private_ip(address):
        raise SecurityError("Only private range IP addresses are allowed.")

def get_private_bind_ip() -> str:
    """Find a local private IP address, defaulting to 127.0.0.1 if none found."""
    for target in ["10.255.255.255", "192.168.255.255", "172.31.255.255"]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            ip = s.getsockname()[0]
            if is_private_ip(ip):
                return ip
        except Exception:
            pass
        finally:
            s.close()
    return "127.0.0.1"

class PeerDiscovery:
    """Handles UDP presence broadcasts and peer discovery on LAN."""
    
    def __init__(self, peer_id: str, display_name: str, discovery_port: int = 5002, bind_address: str | None = None) -> None:
        self.peer_id = peer_id
        self.display_name = display_name
        self.discovery_port = discovery_port
        self.bind_address = bind_address

    def broadcast_presence(self, port: int, broadcast_address: str = "255.255.255.255") -> None:
        """Broadcast peer presence over UDP."""
        payload = {
            "peer_id": self.peer_id,
            "display_name": self.display_name,
            "port": port
        }
        data = json.dumps(payload).encode("utf-8")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Send broadcast to LAN
            sock.sendto(data, (broadcast_address, self.discovery_port))
        finally:
            sock.close()

    def listen_for_peers(self, timeout: float) -> list[PeerInfo]:
        """Listen for UDP presence broadcasts from other peers."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        bind_ip = self.bind_address or get_private_bind_ip()
        validate_bind_address(bind_ip)
        
        sock.bind((bind_ip, self.discovery_port))
        sock.settimeout(timeout)
        
        peers: list[PeerInfo] = []
        seen_peer_ids: set[str] = set()
        
        import time
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                data, addr = sock.recvfrom(65535)
                sender_ip, _ = addr
                if not is_private_ip(sender_ip):
                    continue
                    
                payload = json.loads(data.decode("utf-8"))
                peer_id = payload["peer_id"]
                display_name = payload["display_name"]
                tcp_port = int(payload["port"])
                
                if peer_id == self.peer_id:
                    continue  # Ignore self-broadcasts
                    
                if peer_id not in seen_peer_ids:
                    seen_peer_ids.add(peer_id)
                    peers.append(PeerInfo(
                        address=sender_ip,
                        port=tcp_port,
                        peer_id=peer_id,
                        display_name=display_name
                    ))
            except socket.timeout:
                break
            except Exception:
                break
        sock.close()
        return peers

class PeerSession:
    """Manages an active peer-to-peer TCP connection with integrity and cap checks."""
    
    def __init__(self, sock: socket.socket | None = None, peer_info: PeerInfo | None = None,
                 secret_key: bytes = b"NeonDAW_P2P_Secret_Key", local_peer_info: PeerInfo | None = None) -> None:
        self.sock = sock
        self.peer = peer_info
        self.secret_key = secret_key
        self.local_peer_info = local_peer_info

    def connect(self, peer: PeerInfo) -> bool:
        """Establish a TCP connection to a peer."""
        validate_bind_address(peer.address)
        self.peer = peer
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((peer.address, peer.port))
            # Send local identity info as handshake
            if self.local_peer_info:
                handshake = {
                    "peer_id": self.local_peer_info.peer_id,
                    "display_name": self.local_peer_info.display_name
                }
                self.send_message(json.dumps(handshake).encode("utf-8"))
            return True
        except Exception:
            self.sock = None
            return False

    def send_message(self, message: bytes) -> None:
        """Send a length-prefixed, HMAC-signed message."""
        if not self.sock:
            raise OSError("Socket is not connected.")
        signature = hmac.new(self.secret_key, message, hashlib.sha256).digest()
        length_prefix = len(message).to_bytes(4, byteorder="big")
        self.sock.sendall(length_prefix + signature + message)

    def receive_message(self) -> bytes:
        """Receive the next message, validating size and HMAC signature."""
        if not self.sock:
            raise OSError("Socket is not connected.")
            
        length_bytes = self._read_exact(4)
        length = int.from_bytes(length_bytes, byteorder="big")
        
        # Max message size limit check (10MB)
        if length > 10 * 1024 * 1024:
            raise SecurityError("Message size exceeds maximum limit of 10MB.")
            
        signature = self._read_exact(32)
        payload = self._read_exact(length)
        
        # Integrity validation
        expected_signature = hmac.new(self.secret_key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise SecurityError("HMAC signature verification failed. Message may be tampered.")
            
        return payload

    def disconnect(self) -> None:
        """Cleanly close the TCP connection."""
        if self.sock:
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.sock.close()
            self.sock = None

    def _read_exact(self, n: int) -> bytes:
        data = b""
        while len(data) < n:
            packet = self.sock.recv(n - len(data))
            if not packet:
                raise OSError("Connection closed before reading complete data.")
            data += packet
        return data

class NetworkManager:
    """High-level manager for TCP server hosting and session tracking."""
    
    def __init__(self, local_peer_info: PeerInfo, secret_key: bytes = b"NeonDAW_P2P_Secret_Key",
                 bind_address: str | None = None) -> None:
        self.local_peer_info = local_peer_info
        self.secret_key = secret_key
        self.bind_address = bind_address
        self.server_sock: socket.socket | None = None
        self.sessions: list[PeerSession] = []
        self.is_hosting = False
        self._thread = None

    def start_hosting(self, port: int) -> None:
        """Start accepting connections on the specified port in a background thread."""
        bind_ip = self.bind_address or get_private_bind_ip()
        validate_bind_address(bind_ip)
        
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((bind_ip, port))
        self.server_sock.listen(5)
        
        self.is_hosting = True
        import threading
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop_hosting(self) -> None:
        """Stop hosting and close all active sessions."""
        self.is_hosting = False
        if self.server_sock:
            self.server_sock.close()
            self.server_sock = None
        for session in list(self.sessions):
            session.disconnect()
        self.sessions.clear()

    def get_connected_peers(self) -> list[PeerInfo]:
        """Get information on all currently connected peers."""
        # Clean up dead sessions first
        active_sessions = []
        peers = []
        for session in self.sessions:
            if session.sock:
                try:
                    # Quick check to see if socket is still open
                    session.sock.getpeername()
                    active_sessions.append(session)
                    if session.peer:
                        peers.append(session.peer)
                except Exception:
                    session.disconnect()
            else:
                session.disconnect()
        self.sessions = active_sessions
        return peers

    def _accept_loop(self) -> None:
        while self.is_hosting:
            try:
                client_sock, addr = self.server_sock.accept()
                sender_ip, _ = addr
                if not is_private_ip(sender_ip):
                    client_sock.close()
                    continue
                    
                session = PeerSession(
                    sock=client_sock,
                    secret_key=self.secret_key,
                    local_peer_info=self.local_peer_info
                )
                
                # Handshake: Send local identity first
                handshake = {
                    "peer_id": self.local_peer_info.peer_id,
                    "display_name": self.local_peer_info.display_name
                }
                session.send_message(json.dumps(handshake).encode("utf-8"))
                
                # Receive client's identity
                data = session.receive_message()
                info = json.loads(data.decode("utf-8"))
                session.peer = PeerInfo(
                    address=sender_ip,
                    port=addr[1],
                    peer_id=info["peer_id"],
                    display_name=info["display_name"]
                )
                self.sessions.append(session)
            except Exception:
                if not self.is_hosting:
                    break
