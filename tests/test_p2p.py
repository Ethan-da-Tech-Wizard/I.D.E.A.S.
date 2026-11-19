# ==============================================================================
# Program: tests/test_p2p.py
# Programmer: Ethan-da-Tech-Wizard
# Last Modified: November 18, 2026
# Purpose: tests app/network/p2p.py, the LAN-only peer collaboration layer: private-address checks,
# bind validation, UDP discovery, HMAC-signed framed TCP messages and the host/connect handshake.
# These tests open REAL sockets, but only on 127.0.0.1 with fixed high ports (15002-15004) or
# ephemeral ones; nothing leaves the machine.
# Run: python3 -m pytest tests/test_p2p.py -q       (fixed ports must be free)
# ==============================================================================

import pytest
import socket
import json
import time
import threading
from app.security import SecurityError
from app.network.p2p import (
    PeerInfo, is_private_ip, validate_bind_address, PeerDiscovery, PeerSession, NetworkManager
)

# Policy table for is_private_ip: loopback and the RFC 1918 ranges are allowed; public addresses
# and the wildcard/broadcast addresses are not, so the app never listens on or talks to the internet.
def test_ip_validation():
    # Valid private IPs
    assert is_private_ip("127.0.0.1") is True
    assert is_private_ip("192.168.1.1") is True
    assert is_private_ip("10.0.0.1") is True
    assert is_private_ip("172.16.0.1") is True

    # Wildcard (0.0.0.0, ::) would expose a listener on every interface, so it is rejected too.
    # Invalid public or wildcard IPs
    assert is_private_ip("8.8.8.8") is False
    assert is_private_ip("0.0.0.0") is False
    assert is_private_ip("::") is False
    assert is_private_ip("255.255.255.255") is False

# Same policy enforced as an exception for code that is about to bind a socket.
def test_validate_bind_address():
    # Safe binds
    validate_bind_address("127.0.0.1")
    validate_bind_address("192.168.1.2")
    
    # Dangerous binds/connections
    with pytest.raises(SecurityError):
        validate_bind_address("0.0.0.0")
    with pytest.raises(SecurityError):
        validate_bind_address("::")
    with pytest.raises(SecurityError):
        validate_bind_address("8.8.8.8")
    with pytest.raises(SecurityError):
        validate_bind_address("255.255.255.255")

# PeerInfo is a plain value object (address, port, peer id, display name); checks field order.
def test_peer_info_serialization():
    peer = PeerInfo("127.0.0.1", 5001, "peer_abc", "Alice")
    assert peer.address == "127.0.0.1"
    assert peer.port == 5001
    assert peer.peer_id == "peer_abc"
    assert peer.display_name == "Alice"

# Scenario: one PeerDiscovery listens while another announces itself on loopback. The announcement
# (UDP) must carry the sender's id, name and TCP port. Broadcast address is 127.0.0.1 here so the
# test never touches the real LAN.
def test_p2p_discovery_loopback():
    # Fixed test port, away from the app default to avoid clashing with a running app.
    discovery_port = 15002
    disc_sender = PeerDiscovery("peer_a", "Alice", discovery_port=discovery_port, bind_address="127.0.0.1")
    disc_listener = PeerDiscovery("peer_b", "Bob", discovery_port=discovery_port, bind_address="127.0.0.1")

    discovered = []
    # listen_for_peers blocks until timeout, so it runs in a thread while the main thread announces.
    def listen_thread():
        nonlocal discovered
        discovered = disc_listener.listen_for_peers(timeout=1.0)

    t = threading.Thread(target=listen_thread)
    t.start()
    
    # Give listener a brief moment to bind and block on recvfrom
    # Without this the packet could be sent before the listener binds and would be lost (UDP has no retry).
    time.sleep(0.1)
    
    disc_sender.broadcast_presence(port=8080, broadcast_address="127.0.0.1")
    t.join()

    assert len(discovered) > 0
    assert discovered[0].peer_id == "peer_a"
    assert discovered[0].display_name == "Alice"
    assert discovered[0].port == 8080

# Scenario: a signed frame is [4-byte big-endian length][32-byte HMAC-SHA256][payload]. Checks that
# (1) a valid frame round-trips, (2) a frame with a wrong signature is rejected, and (3) a length
# header above the 10 MiB cap is rejected before any payload is read (stops memory exhaustion).
def test_peer_session_message_integrity_and_tampering():
    # A connected socket pair is built by hand: listen on an ephemeral port (0), accept in a thread
    # while the main thread connects.
    # Setup loopback server and client sockets manually for session testing
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    # Port 0 lets the OS pick a free port, so the test cannot collide with anything.
    server_sock.bind(("127.0.0.1", 0))
    server_sock.listen(1)
    port = server_sock.getsockname()[1]

    client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    
    server_conn = None
    def accept_conn():
        nonlocal server_conn
        server_conn, _ = server_sock.accept()

    t = threading.Thread(target=accept_conn)
    t.start()
    
    client_sock.connect(("127.0.0.1", port))
    t.join()
    server_sock.close()

    # Both ends share the same secret; the signature binds each message to it.
    # Create sessions with matching keys
    key = b"my_super_secret"
    client_session = PeerSession(sock=client_sock, secret_key=key)
    server_session = PeerSession(sock=server_conn, secret_key=key)

    # Valid message
    msg = b"hello world"
    client_session.send_message(msg)
    received = server_session.receive_message()
    assert received == msg

    # Bytes are written straight to the socket to bypass send_message, which would sign correctly.
    # Message tampering - send manually modified signature
    # Signature size is 32 bytes. Send incorrect signature.
    length_prefix = len(msg).to_bytes(4, byteorder="big")
    bad_signature = b"\x00" * 32
    client_sock.sendall(length_prefix + bad_signature + msg)

    with pytest.raises(SecurityError, match="HMAC signature verification failed"):
        server_session.receive_message()

    # Only the 4 length bytes are sent; the receiver must reject on the header alone.
    # Message size cap enforcement
    oversized_length = (10 * 1024 * 1024 + 1).to_bytes(4, byteorder="big")
    client_sock.sendall(oversized_length)
    with pytest.raises(SecurityError, match="Message size exceeds maximum limit"):
        server_session.receive_message()

    client_session.disconnect()
    server_session.disconnect()

# End-to-end handshake: a host manager listens; a client session connects, receives the host's
# identity (JSON) and the host registers the client as a connected peer.
def test_network_manager():
    local_info_host = PeerInfo("127.0.0.1", 15003, "host_peer", "Host")
    local_info_client = PeerInfo("127.0.0.1", 15004, "client_peer", "Client")
    
    key = b"shared_key"
    host_mgr = NetworkManager(local_info_host, secret_key=key, bind_address="127.0.0.1")
    # Hosting runs in a background thread.
    host_mgr.start_hosting(port=15003)
    
    # Give the listener thread time to bind before connecting.
    time.sleep(0.1) # Let the host thread start
    
    client_session = PeerSession(
        secret_key=key,
        local_peer_info=local_info_client
    )
    
    connected = client_session.connect(local_info_host)
    assert connected is True
    
    # Handshake verification: client receives host info
    host_handshake = client_session.receive_message()
    host_data = json.loads(host_handshake.decode("utf-8"))
    assert host_data["peer_id"] == "host_peer"
    assert host_data["display_name"] == "Host"
    
    # The host processes the client's handshake on its own thread, so poll-free sleeping is used here.
    # Allow some time for host manager to accept and process client handshake
    time.sleep(0.2)
    
    peers = host_mgr.get_connected_peers()
    assert len(peers) == 1
    assert peers[0].peer_id == "client_peer"
    assert peers[0].display_name == "Client"
    
    client_session.disconnect()
    host_mgr.stop_hosting()
