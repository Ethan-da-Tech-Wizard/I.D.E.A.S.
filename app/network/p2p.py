# ==============================================================================
# Program: p2p.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: Implements peer-to-peer network discovery and socket transport for multi-user real-time collaborative editing, asset sharing, and synchronized audio playback.
# ==============================================================================
# How this module fits: it is the LAN-only transport layer for the "Collaborate" feature. app/application.py (and the DAW panel)
# create a PeerDiscovery to find other copies of the app, a NetworkManager to host, and a PeerSession per connection.
# Nothing here knows about project data; callers hand it opaque bytes and get opaque bytes back.
# Wire format (both directions): 4-byte big-endian length | 32-byte HMAC-SHA256 of the payload | payload. The payload used for the
# identity handshake is JSON {"peer_id", "display_name"}. Discovery uses separate UDP datagrams carrying JSON {"peer_id", "display_name", "port"}.
# Security design (mirrors app/security.py): only private, loopback-range addresses are accepted for bind, connect and inbound
# peers (is_private_ip); wildcard and broadcast binds are refused; messages above 10 MB are rejected before the payload is read
# so a peer cannot make us allocate arbitrary memory; every message must carry a valid HMAC so tampered data is dropped.
# Known limits (noted, not fixed in this comment pass):
#   - The default HMAC key is a constant in the source, so it proves integrity only against accidental corruption or outsiders
#     who have not read the code; it is not authentication. There is no nonce or counter, so a captured message can be replayed.
#   - The handshake is asymmetric: the host sends its identity first (NetworkManager._accept_loop) and waits for the client's,
#     but PeerSession.connect() sends the client identity without first reading the host's message. The host's identity then
#     sits unread in the client's receive buffer and is returned as the first "real" message by receive_message().
#   - NetworkManager.sessions is mutated from the accept thread and read from the caller's thread with no lock.
#   - The accept loop performs the handshake inline, so one slow or silent client blocks all later connections (no socket timeout).
#   - listen_for_peers() binds to a specific private address, and on some systems that address does not receive
#     255.255.255.255 broadcasts; discovery may then find nobody even though the peers are on the same network.
# Testing: no automated socket test covers this file; loopback (127.0.0.1) works for manual two-instance runs.

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

# Plain data holder for one remote peer; compared and printed by value thanks to @dataclass.
@dataclass
class PeerInfo:
    # address: IPv4/IPv6 text of the peer; port: its TCP listening port; peer_id: stable unique id used to drop duplicates and our own echoes;
    # display_name: human-friendly label shown in the UI.
    """Dataclass holding information about a peer."""
    address: str
    port: int
    peer_id: str
    display_name: str

def is_private_ip(ip: str) -> bool:
    """Return True only for addresses that are safe to talk to on a local network.

    Args:
        ip: textual address; an IPv6 zone suffix ("fe80::1%en0") is stripped before parsing.

    Returns:
        True for RFC1918 private ranges, link-local and loopback; False for public, multicast, wildcard ("0.0.0.0", "::"),
        broadcast, or anything that does not parse.
    """
    try:
        # The zone id after '%' is not understood by ipaddress.ip_address, so remove it before parsing.
        ip_clean = ip.split('%')[0]
        ip_obj = ipaddress.ip_address(ip_clean)
        # Multicast and "unspecified" (0.0.0.0, ::) are technically flagged private by some Python versions; reject them explicitly
        # because binding or connecting to them would reach many hosts or every interface.
        if ip_obj.is_multicast or ip_obj.is_unspecified:
            return False
        # Belt-and-braces string check: the broadcast address 255.255.255.255 is classified "private" by older ipaddress versions.
        if ip_clean in {"0.0.0.0", "::", "255.255.255.255"}:
            return False
        # loopback is allowed so two instances on one machine (and tests) can talk; is_private also covers link-local ranges.
        return ip_obj.is_private or ip_obj.is_loopback
    # Garbage input is simply "not private"; never raise to the caller because this runs on untrusted datagram source addresses too.
    except ValueError:
        return False

def validate_bind_address(address: str) -> None:
    """Raise SecurityError unless the address is a specific private IP.

    Args:
        address: the address a socket is about to bind or connect to.

    Raises:
        SecurityError: for wildcard/broadcast addresses or any address outside the private/loopback ranges.
    """
    # Checked before the generic private test so the user gets the clearer message about wildcard binds.
    if address in {"0.0.0.0", "::", "255.255.255.255"}:
        raise SecurityError("Wildcard or broadcast address binds/connections are prohibited.")
    if not is_private_ip(address):
        raise SecurityError("Only private range IP addresses are allowed.")

def get_private_bind_ip() -> str:
    """Discover this machine's LAN address without sending any packet.

    Returns:
        A private IPv4 address string, or "127.0.0.1" when no private route is found.
    """
    # Connecting a UDP socket sends nothing; it only makes the OS pick the outgoing interface for that destination, and
    # getsockname() then reveals that interface's address. The three targets are one address inside each RFC1918 block so a
    # machine on any of them gets a hit. Port 1 is arbitrary because no data is ever sent.
    for target in ["10.255.255.255", "192.168.255.255", "172.31.255.255"]:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect((target, 1))
            ip = s.getsockname()[0]
            if is_private_ip(ip):
                return ip
        # Unreachable network or no route on this target: try the next block instead of failing.
        except Exception:
            pass
        # Always close the probe socket; leaking three file descriptors per call would add up if called repeatedly.
        finally:
            s.close()
    # Nothing matched (offline laptop): loopback keeps single-machine hosting working.
    return "127.0.0.1"

# Finds other app instances on the LAN by UDP broadcast; holds only the identity it announces.
class PeerDiscovery:
    """Handles UDP presence broadcasts and peer discovery on LAN."""

    
    def __init__(self, peer_id: str, display_name: str, discovery_port: int = 5002, bind_address: str | None = None) -> None:
        """Remember who we are and which UDP port discovery uses.

        Args:
            peer_id: unique id announced to others and used to ignore our own broadcasts.
            display_name: name shown in other users' peer lists.
            discovery_port: UDP port for presence datagrams (default 5002; every instance must agree on it).
            bind_address: optional explicit local address for listening; None picks the LAN address automatically.
        """
        self.peer_id = peer_id
        self.display_name = display_name
        self.discovery_port = discovery_port
        self.bind_address = bind_address

    def broadcast_presence(self, port: int, broadcast_address: str = "255.255.255.255") -> None:
        """Announce this instance once to the whole LAN.

        Args:
            port: the TCP port other peers should connect to (where NetworkManager is hosting).
            broadcast_address: destination, 255.255.255.255 by default (limited broadcast, never routed beyond the local network).

        Raises:
            OSError: if the datagram cannot be sent (no network interface).
        """
        # Compact JSON keeps the datagram far below a typical 1500-byte network packet limit.
        payload = {
            "peer_id": self.peer_id,
            "display_name": self.display_name,
            "port": port
        }
        data = json.dumps(payload).encode("utf-8")
        # UDP because discovery is fire-and-forget: a lost announcement is just repeated, no connection needed.
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            # Sending to a broadcast address is refused by the OS unless SO_BROADCAST is set.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            # Send broadcast to LAN
            sock.sendto(data, (broadcast_address, self.discovery_port))
        # The socket is closed even if sendto raises, so repeated announcements do not leak descriptors.
        finally:
            sock.close()

    def listen_for_peers(self, timeout: float) -> list[PeerInfo]:
        """Collect announcements for up to `timeout` seconds and return the distinct peers heard.

        Args:
            timeout: total listening time in seconds; also the per-receive socket timeout.

        Returns:
            A list of PeerInfo, one per distinct peer_id, excluding ourselves. Datagrams from non-private senders or with
            malformed JSON are ignored; any unexpected error ends the scan early and returns what was found so far.

        Raises:
            SecurityError: if the chosen bind address is not a private address.
            OSError: if the discovery port cannot be bound (for example, already in use without SO_REUSEADDR support).
        """
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        # SO_REUSEADDR lets several app instances on one machine listen on the same discovery port during local testing.
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        
        # Never listen on a wildcard address: that would expose the UDP port on every interface including public ones.
        bind_ip = self.bind_address or get_private_bind_ip()
        validate_bind_address(bind_ip)
        
        # settimeout makes each recvfrom wake up periodically so the overall deadline below can be enforced.
        sock.bind((bind_ip, self.discovery_port))
        sock.settimeout(timeout)
        
        peers: list[PeerInfo] = []
        seen_peer_ids: set[str] = set()
        
        # Imported here rather than at the top of the file, presumably because it is only needed in this method; behaviour is the same.
        import time
        # Wall-clock deadline for the whole scan; settimeout alone would restart for every received datagram.
        start_time = time.time()
        while time.time() - start_time < timeout:
            try:
                # 65535 is the largest possible UDP payload, so no announcement is ever truncated.
                data, addr = sock.recvfrom(65535)
                sender_ip, _ = addr
                # Drop datagrams from outside the private ranges: a spoofed public sender must not be able to add itself to the peer list.
                if not is_private_ip(sender_ip):
                    continue
                    
                # Any missing key or wrong type raises here and is caught by the broad except below, which ends the scan (see known limits).
                payload = json.loads(data.decode("utf-8"))
                peer_id = payload["peer_id"]
                display_name = payload["display_name"]
                tcp_port = int(payload["port"])
                
                # We hear our own broadcast too; showing ourselves in the peer list would be confusing.
                if peer_id == self.peer_id:
                    continue  # Ignore self-broadcasts
                    
                # Peers repeat their announcement; keep the first sighting only.
                if peer_id not in seen_peer_ids:
                    seen_peer_ids.add(peer_id)
                    peers.append(PeerInfo(
                        address=sender_ip,
                        port=tcp_port,
                        peer_id=peer_id,
                        display_name=display_name
                    ))
            # No datagram within the timeout: the scan is over.
            except socket.timeout:
                break
            # One bad datagram stops the scan rather than being skipped; a stricter loop would continue instead. Noted as a limitation.
            except Exception:
                break
        # Not in a try/finally, so an exception raised before this line (e.g. from validate_bind_address after the socket was created) leaks the socket.
        sock.close()
        return peers

# One live TCP link to one peer; also used by the host for each accepted client.
class PeerSession:
    """Manages an active peer-to-peer TCP connection with integrity and cap checks."""

    
    def __init__(self, sock: socket.socket | None = None, peer_info: PeerInfo | None = None,
                 secret_key: bytes = b"NeonDAW_P2P_Secret_Key", local_peer_info: PeerInfo | None = None) -> None:
        """Wrap an existing socket or prepare to connect later.

        Args:
            sock: an already-connected socket (host side) or None (client side, set by connect()).
            peer_info: the remote peer, if known.
            secret_key: shared bytes for HMAC signing. The default is a constant in the source, which gives integrity but not real authentication.
            local_peer_info: our identity, sent as the first message by connect().
        """
        self.sock = sock
        self.peer = peer_info
        self.secret_key = secret_key
        self.local_peer_info = local_peer_info

    def connect(self, peer: PeerInfo) -> bool:
        """Open a TCP connection to `peer` and send our identity.

        Args:
            peer: where to connect; its address must be a private IP or SecurityError is raised before any socket is opened.

        Returns:
            True on success, False if the connection or handshake failed (self.sock is then None).

        Raises:
            SecurityError: when the peer address is not in a private range.
        """
        # Validation happens before socket creation so a hostile or typo address can never trigger an outbound connection to the internet.
        validate_bind_address(peer.address)
        self.peer = peer
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.sock.connect((peer.address, peer.port))
            # Client half of the identity exchange; see the module header for the ordering problem with the host's own greeting.
            # Send local identity info as handshake
            if self.local_peer_info:
                handshake = {
                    "peer_id": self.local_peer_info.peer_id,
                    "display_name": self.local_peer_info.display_name
                }
                self.send_message(json.dumps(handshake).encode("utf-8"))
            return True
        # Swallowing every error turns any failure (refused, timeout, signing error) into a plain False for the UI; the details are lost.
        except Exception:
            self.sock = None
            return False

    def send_message(self, message: bytes) -> None:
        """Sign and send one framed message.

        Args:
            message: raw payload bytes.

        Raises:
            OSError: if not connected or the send fails.
        """
        if not self.sock:
            raise OSError("Socket is not connected.")
        # HMAC over the payload only (not the length), so tampering with the length would be caught indirectly: the receiver would read the wrong
        # number of bytes and the signature check would fail.
        signature = hmac.new(self.secret_key, message, hashlib.sha256).digest()
        # 4 bytes (big-endian) allow up to about 4 GiB, but the receiver enforces a 10 MB cap well below that.
        length_prefix = len(message).to_bytes(4, byteorder="big")
        # Single sendall so a frame is written as one unit; it is not safe to call from two threads at once (frames could interleave).
        self.sock.sendall(length_prefix + signature + message)

    def receive_message(self) -> bytes:
        """Read one frame and return its verified payload.

        Returns:
            The payload bytes.

        Raises:
            OSError: not connected, or the peer closed mid-frame.
            SecurityError: declared length over 10 MB, or the HMAC does not match.
        """
        if not self.sock:
            raise OSError("Socket is not connected.")
            
        # Always read exactly 4 bytes first; TCP is a stream, so a single recv could return a partial header.
        length_bytes = self._read_exact(4)
        length = int.from_bytes(length_bytes, byteorder="big")
        
        # Reject oversized frames before allocating or reading the payload; otherwise a peer could announce 4 GB and exhaust memory.
        # Max message size limit check (10MB)
        if length > 10 * 1024 * 1024:
            raise SecurityError("Message size exceeds maximum limit of 10MB.")
            
        # Signature precedes the payload on the wire (32 bytes = SHA-256 digest size).
        signature = self._read_exact(32)
        payload = self._read_exact(length)
        
        # compare_digest takes constant time, so a timing side channel cannot be used to guess a valid signature byte by byte.
        # Integrity validation
        expected_signature = hmac.new(self.secret_key, payload, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected_signature):
            raise SecurityError("HMAC signature verification failed. Message may be tampered.")
            
        return payload

    def disconnect(self) -> None:
        """Shut down and close the connection; safe to call repeatedly."""
        if self.sock:
            # shutdown wakes any thread blocked in recv on this socket; it fails harmlessly if the peer already closed.
            try:
                self.sock.shutdown(socket.SHUT_RDWR)
            except Exception:
                pass
            self.sock.close()
            self.sock = None

    def _read_exact(self, n: int) -> bytes:
        """Read exactly `n` bytes from the stream, looping over short reads.

        Args:
            n: number of bytes wanted.

        Raises:
            OSError: if the peer closes the connection before `n` bytes arrive.
        """
        data = b""
        while len(data) < n:
            # recv may return fewer bytes than asked; keep reading the remainder until the frame is complete.
            packet = self.sock.recv(n - len(data))
            # An empty read means an orderly close by the peer; without this check the loop would spin forever.
            if not packet:
                raise OSError("Connection closed before reading complete data.")
            data += packet
        return data

# Owns the listening socket and the list of accepted sessions; the host side of a collaboration session.
class NetworkManager:
    """High-level manager for TCP server hosting and session tracking."""

    
    def __init__(self, local_peer_info: PeerInfo, secret_key: bytes = b"NeonDAW_P2P_Secret_Key",
                 bind_address: str | None = None) -> None:
        """Prepare (but do not start) a host.

        Args:
            local_peer_info: identity sent to every client that connects.
            secret_key: shared HMAC key; must equal the clients' key or every message is rejected.
            bind_address: private address to listen on; None picks the LAN address automatically.
        """
        self.local_peer_info = local_peer_info
        self.secret_key = secret_key
        self.bind_address = bind_address
        self.server_sock: socket.socket | None = None
        self.sessions: list[PeerSession] = []
        self.is_hosting = False
        self._thread = None

    def start_hosting(self, port: int) -> None:
        """Open the listening socket and start the accept thread.

        Args:
            port: TCP port to listen on (0 would let the OS pick one, but the chosen port is not reported back).

        Raises:
            SecurityError: the bind address is not private.
            OSError: the port is already in use.
        """
        bind_ip = self.bind_address or get_private_bind_ip()
        # Refuse a wildcard bind so the host is never reachable from outside the local network.
        validate_bind_address(bind_ip)
        
        self.server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # Lets the host restart quickly on the same port without waiting for old connections to leave TIME_WAIT.
        self.server_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.server_sock.bind((bind_ip, port))
        # Backlog of 5 pending connections is plenty for a small collaboration group.
        self.server_sock.listen(5)
        
        self.is_hosting = True
        # Imported lazily here; behaviour identical to a top-level import.
        import threading
        # Daemon thread so a forgotten stop_hosting() never keeps the process alive after the window closes.
        self._thread = threading.Thread(target=self._accept_loop, daemon=True)
        self._thread.start()

    def stop_hosting(self) -> None:
        """Stop accepting connections and disconnect every session. Safe to call twice."""
        # Clearing the flag first lets the accept loop exit quietly when closing the socket makes accept() raise.
        self.is_hosting = False
        if self.server_sock:
            self.server_sock.close()
            self.server_sock = None
        # Iterate over a copy because disconnect() and the accept thread may touch the same list.
        for session in list(self.sessions):
            session.disconnect()
        self.sessions.clear()

    def get_connected_peers(self) -> list[PeerInfo]:
        """Return the peers whose sockets are still open, pruning dead sessions as a side effect.

        Returns:
            PeerInfo for each live session that has finished the handshake.
        """
        # Housekeeping and query in one pass: the list is rebuilt with only live sessions and assigned back at the end.
        # Clean up dead sessions first
        active_sessions = []
        peers = []
        for session in self.sessions:
            if session.sock:
                try:
                    # getpeername raises OSError on a closed or reset socket, so it works as a cheap liveness probe without sending data.
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
        """Accept loop run on the background thread; one client is handshaken at a time.

        Runs until is_hosting is False. Errors from one client are swallowed so a bad peer cannot stop hosting.
        """
        while self.is_hosting:
            try:
                client_sock, addr = self.server_sock.accept()
                # Closing the listening socket from stop_hosting makes accept() raise, which ends the loop below.
                sender_ip, _ = addr
                # Same LAN-only rule as discovery: drop connections from public addresses at once.
                if not is_private_ip(sender_ip):
                    client_sock.close()
                    continue
                    
                session = PeerSession(
                    sock=client_sock,
                    secret_key=self.secret_key,
                    local_peer_info=self.local_peer_info
                )
                
                # Host speaks first: tells the client who it is. The client side (PeerSession.connect) does not read this; see the module header.
                # Handshake: Send local identity first
                handshake = {
                    "peer_id": self.local_peer_info.peer_id,
                    "display_name": self.local_peer_info.display_name
                }
                session.send_message(json.dumps(handshake).encode("utf-8"))
                
                # Then wait for the client's identity. Blocks with no timeout, so a client that connects and stays silent stalls the loop.
                # Receive client's identity
                data = session.receive_message()
                info = json.loads(data.decode("utf-8"))
                session.peer = PeerInfo(
                    address=sender_ip,
                    # The recorded port is the client's ephemeral source port, not a port it listens on; only the address is useful for reconnecting.
                    port=addr[1],
                    peer_id=info["peer_id"],
                    display_name=info["display_name"]
                )
                self.sessions.append(session)
            # A failed handshake (bad HMAC, bad JSON) is dropped; the half-set-up client socket is not closed here, so it lingers until garbage-collected.
            except Exception:
                if not self.is_hosting:
                    break
