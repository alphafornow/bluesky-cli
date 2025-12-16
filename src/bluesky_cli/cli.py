#!/usr/bin/env python3
"""
Bluesky CLI - Alpha's command-line interface for Bluesky.

A simple CLI for posting, reading timeline, and interacting with Bluesky.
"""

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from atproto import Client
from atproto.exceptions import AtProtocolError


# Session cache location
SESSION_FILE = Path.home() / ".cache" / "bsky" / "session.txt"


def save_session(client: Client) -> None:
    """Save the current session to disk using SDK's export."""
    SESSION_FILE.parent.mkdir(parents=True, exist_ok=True)
    session_string = client.export_session_string()
    SESSION_FILE.write_text(session_string)


def load_session() -> str | None:
    """Load a saved session string from disk."""
    if not SESSION_FILE.exists():
        return None
    try:
        return SESSION_FILE.read_text().strip()
    except IOError:
        return None


def clear_session() -> None:
    """Clear the saved session."""
    if SESSION_FILE.exists():
        SESSION_FILE.unlink()


def get_client() -> Client:
    """Create and authenticate a Bluesky client, using cached session if available."""
    # Try to restore from cached session first
    session_string = load_session()
    if session_string:
        try:
            client = Client()
            client.login(session_string=session_string)
            # Update the saved session in case tokens were refreshed
            save_session(client)
            return client
        except Exception:
            # Session expired or invalid, clear it and fall through to fresh login
            clear_session()

    # Fresh login required
    handle = os.environ.get("BLUESKY_HANDLE")
    password = os.environ.get("BLUESKY_APP_PASSWORD")

    if not handle or not password:
        click.echo("Error: BLUESKY_HANDLE and BLUESKY_APP_PASSWORD must be set", err=True)
        sys.exit(1)

    try:
        client = Client()
        client.login(handle, password)
        # Save session for future use
        save_session(client)
        click.echo(f"✓ Logged in as @{client.me.handle} (session cached)", err=True)
    except AtProtocolError as e:
        click.echo(f"Login failed: {e}", err=True)
        sys.exit(1)

    return client


def format_post(post, show_uri: bool = False) -> str:
    """Format a post for display."""
    record = post.post.record
    author = post.post.author

    # Handle the timestamp
    created = record.created_at
    if isinstance(created, str):
        # Parse ISO format string
        try:
            dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
            time_str = dt.strftime("%Y-%m-%d %H:%M")
        except ValueError:
            time_str = created
    else:
        time_str = str(created)

    lines = []
    lines.append(f"@{author.handle} ({author.display_name or author.handle})")
    lines.append(f"  {record.text}")
    lines.append(f"  {time_str}")

    # Engagement stats
    like_count = post.post.like_count or 0
    repost_count = post.post.repost_count or 0
    reply_count = post.post.reply_count or 0
    stats = f"  ♥ {like_count}  🔁 {repost_count}  💬 {reply_count}"
    lines.append(stats)

    if show_uri:
        lines.append(f"  URI: {post.post.uri}")

    return "\n".join(lines)


@click.group()
@click.version_option()
def cli():
    """Bluesky CLI - Post, read, and interact with Bluesky from the command line."""
    pass


@cli.command()
@click.argument("text")
def post(text: str):
    """Create a new post.

    TEXT is the content of your post (max 300 characters).
    """
    if len(text) > 300:
        click.echo(f"Error: Post too long ({len(text)} chars, max 300)", err=True)
        sys.exit(1)

    client = get_client()
    try:
        result = client.send_post(text=text)
        click.echo(f"✓ Posted: {text[:50]}{'...' if len(text) > 50 else ''}")
        click.echo(f"  URI: {result.uri}")
    except AtProtocolError as e:
        click.echo(f"Failed to post: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.option("-n", "--limit", default=20, help="Number of posts to show")
@click.option("--uri", is_flag=True, help="Show post URIs (for replying)")
def timeline(limit: int, uri: bool):
    """Show your home timeline."""
    client = get_client()
    try:
        feed = client.get_timeline(limit=limit)
        click.echo(f"# Timeline ({len(feed.feed)} posts)\n")
        for item in feed.feed:
            click.echo(format_post(item, show_uri=uri))
            click.echo()
    except AtProtocolError as e:
        click.echo(f"Failed to get timeline: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("post_uri")
@click.argument("text")
def reply(post_uri: str, text: str):
    """Reply to a post.

    POST_URI is the at:// URI of the post to reply to.
    TEXT is your reply (max 300 characters).
    """
    if len(text) > 300:
        click.echo(f"Error: Reply too long ({len(text)} chars, max 300)", err=True)
        sys.exit(1)

    client = get_client()
    try:
        # Get the parent post to get its CID
        thread = client.get_post_thread(uri=post_uri)
        parent = thread.thread.post

        # Create reply reference
        reply_ref = {
            "root": {"uri": parent.uri, "cid": parent.cid},
            "parent": {"uri": parent.uri, "cid": parent.cid},
        }

        result = client.send_post(text=text, reply_to=reply_ref)
        click.echo(f"✓ Replied: {text[:50]}{'...' if len(text) > 50 else ''}")
        click.echo(f"  URI: {result.uri}")
    except AtProtocolError as e:
        click.echo(f"Failed to reply: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("post_uri")
def like(post_uri: str):
    """Like a post.

    POST_URI is the at:// URI of the post to like.
    """
    client = get_client()
    try:
        # Get the post to get its CID
        thread = client.get_post_thread(uri=post_uri)
        post = thread.thread.post

        client.like(uri=post.uri, cid=post.cid)
        click.echo(f"✓ Liked post by @{post.author.handle}")
    except AtProtocolError as e:
        click.echo(f"Failed to like: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("post_uri")
def thread(post_uri: str):
    """Show a post thread.

    POST_URI is the at:// URI of the post.
    """
    client = get_client()
    try:
        result = client.get_post_thread(uri=post_uri)
        thread_post = result.thread

        # Show parent posts if any
        if hasattr(thread_post, "parent") and thread_post.parent:
            click.echo("--- Parent ---")
            parent = thread_post.parent
            if hasattr(parent, "post"):
                click.echo(f"@{parent.post.author.handle}: {parent.post.record.text}")
            click.echo()

        # Show main post
        click.echo("--- Post ---")
        post = thread_post.post
        click.echo(f"@{post.author.handle} ({post.author.display_name or post.author.handle})")
        click.echo(f"  {post.record.text}")
        click.echo(f"  ♥ {post.like_count or 0}  🔁 {post.repost_count or 0}  💬 {post.reply_count or 0}")
        click.echo()

        # Show replies if any
        if hasattr(thread_post, "replies") and thread_post.replies:
            click.echo("--- Replies ---")
            for reply in thread_post.replies[:10]:
                if hasattr(reply, "post"):
                    click.echo(f"@{reply.post.author.handle}: {reply.post.record.text[:100]}")
                    click.echo()

    except AtProtocolError as e:
        click.echo(f"Failed to get thread: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("query")
@click.option("-n", "--limit", default=20, help="Number of results")
def search(query: str, limit: int):
    """Search for posts.

    QUERY is your search term.
    """
    client = get_client()
    try:
        results = client.app.bsky.feed.search_posts(
            params={"q": query, "limit": limit}
        )
        click.echo(f"# Search: '{query}' ({len(results.posts)} results)\n")
        for post in results.posts:
            click.echo(f"@{post.author.handle}: {post.record.text[:100]}...")
            click.echo(f"  ♥ {post.like_count or 0}  URI: {post.uri}")
            click.echo()
    except AtProtocolError as e:
        click.echo(f"Failed to search: {e}", err=True)
        sys.exit(1)


@cli.command()
@click.argument("handle", required=False)
def profile(handle: str | None):
    """Show a user's profile.

    HANDLE is the user's handle (e.g., alice.bsky.social). Omit to show your own.
    """
    client = get_client()
    try:
        if handle is None:
            # Show own profile
            handle = client.me.handle

        result = client.get_profile(handle)

        click.echo(f"# @{result.handle}")
        if result.display_name:
            click.echo(f"  {result.display_name}")
        if result.description:
            click.echo(f"  {result.description}")
        click.echo()
        click.echo(f"  Followers: {result.followers_count}")
        click.echo(f"  Following: {result.follows_count}")
        click.echo(f"  Posts: {result.posts_count}")

    except AtProtocolError as e:
        click.echo(f"Failed to get profile: {e}", err=True)
        sys.exit(1)


@cli.command()
def whoami():
    """Show your authenticated account info."""
    client = get_client()
    click.echo(f"Logged in as: @{client.me.handle}")
    click.echo(f"DID: {client.me.did}")
    if SESSION_FILE.exists():
        click.echo(f"Session cached at: {SESSION_FILE}")


@cli.command()
def logout():
    """Clear cached session (forces fresh login on next command)."""
    if SESSION_FILE.exists():
        clear_session()
        click.echo("✓ Session cleared")
    else:
        click.echo("No cached session found")


def main():
    """Entry point."""
    cli()


if __name__ == "__main__":
    main()
