#!/usr/bin/env python3
"""
Interactive Server Restart Manager
Restart the backend and MCP servers from a menu.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# MCP server host
MCP_HOST = "192.168.1.117"
# Backend host (also serves the kiosk artifact)
BACKEND_HOST = "192.168.1.111"
BACKEND_SERVICE = "chat-backend"

# Project root (parent of scripts/)
PROJECT_ROOT = Path(__file__).parent.parent

# Load MCP server config
DATA_DIR = PROJECT_ROOT / "data"
MCP_SERVERS_FILE = DATA_DIR / "mcp_servers.json"


def load_mcp_servers() -> list[dict]:
    """Load MCP servers from config file."""
    if not MCP_SERVERS_FILE.exists():
        return []
    with open(MCP_SERVERS_FILE) as f:
        data = json.load(f)
    return data.get("servers", [])


def get_service_name(server_id: str) -> str:
    """Convert server ID to systemd service name."""
    return f"mcp-{server_id}"


def ssh_restart(host: str, service: str) -> tuple[bool, str]:
    """Restart a service via SSH."""
    cmd = ["ssh", f"root@{host}", f"systemctl restart {service}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, f"✅ Restarted {service} on {host}"
        else:
            return False, f"❌ Failed: {result.stderr.strip()}"
    except subprocess.TimeoutExpired:
        return False, f"❌ Timeout restarting {service}"
    except Exception as e:
        return False, f"❌ Error: {e}"


def ssh_status(host: str, service: str) -> str:
    """Get service status via SSH."""
    cmd = ["ssh", f"root@{host}", f"systemctl is-active {service}"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        return result.stdout.strip()
    except Exception:
        return "unknown"


def print_header():
    print("\n" + "=" * 60)
    print("🔄 SERVER RESTART MANAGER")
    print("=" * 60)


def print_menu(mcp_servers: list[dict]):
    print("\n📋 Available Servers:\n")

    # Backend
    print("  --- Backend ---")
    print("  [0] Backend API (192.168.1.111)")

    # MCP servers
    print("\n  --- MCP Servers ---")
    for i, server in enumerate(mcp_servers, 1):
        status = "✅" if server.get("enabled") else "⚪"
        # Extract port from URL
        url = server.get("url", "")
        port = ""
        if "192.168.1.117:" in url:
            port = url.split(":")[2].split("/")[0]
            port = f":{port}"
        print(f"  [{i}] {status} {server['id']:<15} ({MCP_HOST}{port})")

    print("\n  --- Bulk Actions ---")
    print("  [A] Restart ALL MCP servers")
    print("  [B] Restart Backend + ALL MCP")
    print("  [X] Restart backend + ALL MCP servers")
    print("  [S] Show server status")
    print("  [Q] Quit")
    print()


def restart_backend() -> tuple[bool, str]:
    """Restart the backend API server."""
    return ssh_restart(BACKEND_HOST, BACKEND_SERVICE)


def restart_mcp_server(server_id: str) -> tuple[bool, str]:
    """Restart a specific MCP server."""
    service = get_service_name(server_id)
    return ssh_restart(MCP_HOST, service)


def restart_all_mcp(servers: list[dict]) -> list[tuple[str, bool, str]]:
    """Restart all MCP servers."""
    results = []
    for server in servers:
        if server.get("enabled"):
            server_id = server["id"]
            success, msg = restart_mcp_server(server_id)
            results.append((server_id, success, msg))
    return results


def show_status(mcp_servers: list[dict]):
    """Show status of all servers."""
    print("\n📊 Server Status:\n")

    # Backend status
    status = ssh_status(BACKEND_HOST, BACKEND_SERVICE)
    icon = "🟢" if status == "active" else "🔴"
    print(f"  {icon} Backend API: {status}")

    # MCP server status
    print("\n  MCP Servers:")
    for server in mcp_servers:
        if server.get("enabled"):
            service = get_service_name(server["id"])
            status = ssh_status(MCP_HOST, service)
            icon = "🟢" if status == "active" else "🔴"
            print(f"  {icon} {server['id']:<15}: {status}")


def main():
    servers = load_mcp_servers()

    if not servers:
        print("❌ No MCP servers found in config")
        sys.exit(1)

    # Filter to only account/home MCP servers on LXC 117.
    local_servers = [s for s in servers if MCP_HOST in s.get("url", "")]

    while True:
        print_header()
        print_menu(local_servers)

        choice = input("Select server to restart: ").strip().upper()

        if choice == "Q":
            print("\n👋 Goodbye!")
            break

        elif choice == "S":
            show_status(local_servers)

        elif choice == "0":
            print("\n🔄 Restarting Backend API...")
            success, msg = restart_backend()
            print(msg)

        elif choice == "A":
            print("\n🔄 Restarting ALL MCP servers...")
            results = restart_all_mcp(local_servers)
            for server_id, success, msg in results:
                print(msg)

        elif choice == "B":
            print("\n🔄 Restarting Backend + ALL MCP servers...")
            success, msg = restart_backend()
            print(msg)
            results = restart_all_mcp(local_servers)
            for server_id, success, msg in results:
                print(msg)

        elif choice == "X":
            print("\n🔄 Restarting backend + ALL MCP servers...")
            success, msg = restart_backend()
            print(msg)
            results = restart_all_mcp(local_servers)
            for server_id, success, msg in results:
                print(msg)

        elif choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(local_servers):
                server = local_servers[idx]
                print(f"\n🔄 Restarting {server['id']}...")
                success, msg = restart_mcp_server(server["id"])
                print(msg)
            else:
                print("❌ Invalid selection")

        else:
            print("❌ Invalid option")

        input("\nPress Enter to continue...")


if __name__ == "__main__":
    main()
