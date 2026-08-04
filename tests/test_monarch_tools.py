"""Test script to verify Monarch tools are working correctly."""

__test__ = False

import asyncio
import json
from datetime import date
from pathlib import Path

from monarchmoney import MonarchMoney

from backend.services.monarch_auth import get_monarch_credentials


async def test_tool(name: str, coro):
    """Test a single tool and print results."""
    print(f"\n{'=' * 80}")
    print(f"Testing: {name}")
    print("=" * 80)
    try:
        result = await coro
        print("✓ Success")
        if isinstance(result, dict):
            print(f"Keys: {list(result.keys())}")
            # Print first few items if it's a list
            for key, value in result.items():
                if isinstance(value, list) and len(value) > 0:
                    print(f"{key}: {len(value)} items")
                    print(
                        f"  First item: {json.dumps(value[0], indent=2, default=str)[:200]}..."
                    )
                elif isinstance(value, dict) and value:
                    print(f"{key}: {json.dumps(value, indent=2, default=str)[:200]}...")
                else:
                    print(f"{key}: {value}")
    except Exception as e:
        print(f"✗ Error: {e}")


async def main():
    """Test various Monarch tools."""
    creds = get_monarch_credentials()
    if not creds:
        print("No credentials found")
        return

    session_file = Path("data/tokens/monarch_session.pickle")
    mm = MonarchMoney(session_file=str(session_file))

    # Try to load existing session
    if session_file.exists():
        try:
            mm.load_session(str(session_file))
        except Exception:
            pass

    # Check if logged in
    try:
        await mm.get_subscription_details()
        print("✓ Already logged in")
    except Exception:
        print("Logging in...")
        mfa_secret = (
            creds.mfa_secret.strip().replace(" ", "") if creds.mfa_secret else None
        )
        await mm.login(
            email=creds.email,
            password=creds.password,
            mfa_secret_key=mfa_secret,
            use_saved_session=False,
        )
        mm.save_session(str(session_file))
        print("✓ Logged in")

    # Test get_budgets
    await test_tool("get_budgets", mm.get_budgets())

    # Test get_recurring_transactions
    await test_tool(
        "get_recurring_transactions",
        mm.get_recurring_transactions(start_date="2025-11-01", end_date="2025-12-31"),
    )

    # Test get_transactions_summary
    await test_tool("get_transactions_summary", mm.get_transactions_summary())

    # Test get_aggregate_snapshots
    await test_tool(
        "get_aggregate_snapshots",
        mm.get_aggregate_snapshots(
            start_date=date(2025, 11, 1), end_date=date(2025, 11, 19)
        ),
    )


if __name__ == "__main__":
    asyncio.run(main())
