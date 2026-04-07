#!/usr/bin/env python3
"""Manage Strava webhook subscriptions."""

import argparse

import httpx

from app.config import settings

SUBSCRIPTIONS_URL = "https://www.strava.com/api/v3/push_subscriptions"


def create_subscription(callback_url: str):
    resp = httpx.post(
        SUBSCRIPTIONS_URL,
        data={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
            "callback_url": f"{callback_url}/webhook",
            "verify_token": settings.STRAVA_VERIFY_TOKEN,
        },
    )
    print(f"Status: {resp.status_code}")
    print(resp.json())


def list_subscriptions():
    resp = httpx.get(
        SUBSCRIPTIONS_URL,
        params={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
        },
    )
    print(resp.json())


def delete_subscription(subscription_id: int):
    resp = httpx.delete(
        f"{SUBSCRIPTIONS_URL}/{subscription_id}",
        params={
            "client_id": settings.STRAVA_CLIENT_ID,
            "client_secret": settings.STRAVA_CLIENT_SECRET,
        },
    )
    print(f"Deleted: {resp.status_code}")


def main():
    parser = argparse.ArgumentParser(description="Manage Strava webhook subscriptions")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--create", action="store_true", help="Create a new subscription")
    group.add_argument("--list", action="store_true", help="List subscriptions")
    group.add_argument("--delete", type=int, help="Delete a subscription by ID")
    parser.add_argument("--url", type=str, help="Callback URL (required for --create)")

    args = parser.parse_args()

    if args.create:
        if not args.url:
            parser.error("--url is required with --create")
        create_subscription(args.url)
    elif args.list:
        list_subscriptions()
    elif args.delete is not None:
        delete_subscription(args.delete)


if __name__ == "__main__":
    main()
