"""
Discord Bot for monitoring YouTube channels and posting new videos
Multi-monitor support with interactive management buttons
"""

import asyncio
import discord
from discord.ext import commands, tasks
import feedparser
import os
import json
from datetime import datetime

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
POSTED_VIDEOS_FILE = 'posted_videos.json'
BOT_CONFIG_FILE = 'bot_config.json'

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

youtube_check_lock = asyncio.Lock()

# ============ CONFIG MANAGEMENT ============

def load_posted_videos():
    """Load posted video tracking (dict: youtube_channel_id -> latest_video_id)"""
    try:
        if os.path.exists(POSTED_VIDEOS_FILE):
            with open(POSTED_VIDEOS_FILE, 'r') as f:
                data = json.load(f)
                if isinstance(data, list):
                    return {}
                return data
    except (json.JSONDecodeError, FileNotFoundError):
        pass
    return {}

def save_posted_videos(data):
    try:
        with open(POSTED_VIDEOS_FILE, 'w') as f:
            json.dump(data, f)
    except Exception as e:
        print(f"Error saving posted videos: {e}")

def is_posted(youtube_channel_id, video_id):
    data = load_posted_videos()
    return data.get(youtube_channel_id) == video_id

def mark_posted(youtube_channel_id, video_id):
    data = load_posted_videos()
    data[youtube_channel_id] = video_id
    save_posted_videos(data)

def load_config():
    try:
        if os.path.exists(BOT_CONFIG_FILE):
            with open(BOT_CONFIG_FILE, 'r') as f:
                return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        pass
    return {}

def save_config(config):
    try:
        with open(BOT_CONFIG_FILE, 'w') as f:
            json.dump(config, f, indent=2)
    except Exception as e:
        print(f"Error saving config: {e}")

def get_guild_monitors(guild_id):
    """Get all monitors for a guild, migrating old single-monitor format if needed"""
    config = load_config()
    guild_data = config.get(str(guild_id), {})

    if 'monitors' not in guild_data:
        monitors = []
        if guild_data.get('youtube_channel_id') and guild_data.get('notification_channel'):
            monitors.append({
                'youtube_channel_id': guild_data['youtube_channel_id'],
                'youtube_channel_name': guild_data.get('youtube_channel_name', 'Unknown'),
                'discord_channel_id': guild_data['notification_channel']
            })
        config[str(guild_id)] = {'monitors': monitors}
        save_config(config)
        return monitors

    return guild_data.get('monitors', [])

def set_guild_monitors(guild_id, monitors):
    config = load_config()
    config[str(guild_id)] = {'monitors': monitors}
    save_config(config)

def add_monitor(guild_id, youtube_channel_id, youtube_channel_name, discord_channel_id):
    """Add or update a monitor. Returns True if new, False if updated."""
    monitors = get_guild_monitors(guild_id)
    for m in monitors:
        if m['youtube_channel_id'] == youtube_channel_id:
            m['discord_channel_id'] = discord_channel_id
            m['youtube_channel_name'] = youtube_channel_name
            set_guild_monitors(guild_id, monitors)
            return False
    monitors.append({
        'youtube_channel_id': youtube_channel_id,
        'youtube_channel_name': youtube_channel_name,
        'discord_channel_id': discord_channel_id
    })
    set_guild_monitors(guild_id, monitors)
    return True

def remove_monitor(guild_id, youtube_channel_id):
    monitors = get_guild_monitors(guild_id)
    new_monitors = [m for m in monitors if m['youtube_channel_id'] != youtube_channel_id]
    set_guild_monitors(guild_id, new_monitors)
    return len(monitors) != len(new_monitors)

# ============ HELPERS ============

async def post_latest_video(guild, monitor, discord_channel, force=False):
    """
    Fetch and post the latest video for a monitor.
    If force=False, skips if already posted.
    Returns the video dict if posted, None otherwise.
    """
    youtube_channel_id = monitor['youtube_channel_id']
    youtube_channel_name = monitor['youtube_channel_name']

    rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}"
    feed = feedparser.parse(rss_url)

    if not feed.entries:
        return None

    latest_entry = feed.entries[0]

    if not force and is_posted(youtube_channel_id, latest_entry.yt_videoid):
        return None

    latest_video = {
        'id': latest_entry.yt_videoid,
        'title': latest_entry.title,
        'link': latest_entry.link,
        'published': latest_entry.published,
        'author': latest_entry.author
    }

    mark_posted(youtube_channel_id, latest_video['id'])

    news_role = discord.utils.get(guild.roles, name="Yonkagor Daily News")
    role_mention = news_role.mention if news_role else None

    embed = discord.Embed(
        title=latest_video['title'],
        url=latest_video['link'],
        color=discord.Color.red(),
        description=f"New video from {latest_video['author']}"
    )
    embed.set_thumbnail(url=f"https://i.ytimg.com/vi/{latest_video['id']}/maxresdefault.jpg")
    embed.add_field(name="Channel", value=latest_video['author'], inline=True)
    embed.add_field(
        name="Posted",
        value=f"<t:{int(datetime.fromisoformat(latest_video['published'].replace('Z', '+00:00')).timestamp())}:R>",
        inline=True
    )
    embed.set_footer(text=f"Monitoring: {youtube_channel_name}")

    try:
        async for pin_msg in discord_channel.pins():
            if pin_msg.author == bot.user:
                await pin_msg.unpin()
    except Exception as e:
        print(f"Error unpinning: {e}")

    message = await discord_channel.send(content=role_mention, embed=embed)

    try:
        await message.pin()
        print(f"Pinned: {latest_video['title']} in #{discord_channel.name}")
    except discord.Forbidden:
        print(f"No permission to pin in #{discord_channel.name}")
    except Exception as e:
        print(f"Error pinning message: {e}")

    if discord_channel.type == discord.ChannelType.news:
        try:
            await message.publish()
            print(f"Published announcement: {latest_video['title']} in #{discord_channel.name}")
        except discord.Forbidden:
            print(f"No permission to publish in #{discord_channel.name}")
        except Exception as e:
            print(f"Error publishing: {e}")

    print(f"Posted: {latest_video['title']} → {guild.name} / #{discord_channel.name}")
    return latest_video

async def ensure_announcement_channel(channel):
    """Convert a text channel to announcement (news) type if it isn't already. Returns True on success."""
    if channel.type == discord.ChannelType.news:
        return True
    try:
        await channel.edit(type=discord.ChannelType.news)
        print(f"Converted #{channel.name} to announcement channel")
        return True
    except discord.Forbidden:
        print(f"No permission to convert #{channel.name} to announcement")
        return False
    except Exception as e:
        print(f"Error converting #{channel.name} to announcement: {e}")
        return False

async def clear_bot_messages(channel):
    """Delete all messages from the bot in a channel"""
    deleted = 0
    async for message in channel.history(limit=200):
        if message.author == bot.user:
            try:
                await message.delete()
                deleted += 1
                await asyncio.sleep(0.5)
            except Exception:
                pass
    return deleted

# ============ EVENTS ============

@bot.event
async def on_ready():
    print(f'{bot.user} has connected to Discord!')
    check_youtube.start()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="YouTube uploads"
        )
    )
    print("Bot is ready!")

@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("❌ You don't have permission to use this command.")
    elif isinstance(error, commands.CommandNotFound):
        pass
    elif isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"❌ Missing argument. Use `!help_youtube` for usage.")
    else:
        await ctx.send(f"❌ An error occurred: {error}")

# ============ YOUTUBE CHECK TASK ============

@tasks.loop(minutes=5)
async def check_youtube():
    if youtube_check_lock.locked():
        return
    async with youtube_check_lock:
        await _do_youtube_check()

async def _do_youtube_check():
    try:
        for guild in bot.guilds:
            monitors = get_guild_monitors(guild.id)
            for monitor in monitors:
                discord_channel = guild.get_channel(monitor['discord_channel_id'])
                if not discord_channel:
                    continue
                if not discord_channel.permissions_for(guild.me).send_messages:
                    continue
                try:
                    await post_latest_video(guild, monitor, discord_channel, force=False)
                except Exception as e:
                    print(f"Error checking {monitor['youtube_channel_name']} for {guild.name}: {e}")
    except Exception as e:
        print(f"Error in check_youtube task: {e}")

# ============ COMMANDS ============

@bot.command(name='addnew')
@commands.has_permissions(administrator=True)
async def cmd_addnew(ctx, youtube_channel_id: str = None, channel: discord.TextChannel = None):
    """Add a new YouTube channel to monitor: !addnew <youtube_channel_id> <#discord-channel>"""
    if not youtube_channel_id or not channel:
        await ctx.send("❌ Usage: `!addnew <youtube_channel_id> <#discord-channel>`")
        return

    youtube_channel_id = youtube_channel_id.strip()

    if not youtube_channel_id.startswith('UC') or len(youtube_channel_id) != 24:
        embed = discord.Embed(
            title="❌ Invalid Channel ID",
            description=f"`{youtube_channel_id}` is not a valid YouTube channel ID.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Valid format",
            value="Channel IDs start with `UC` and are 24 characters long\nExample: `UCwHjwUSEIwLEShZR1BjbNxg`",
            inline=False
        )
        await ctx.send(embed=embed)
        return

    msg = await ctx.send("🔍 Looking up YouTube channel...")

    feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={youtube_channel_id}")
    if feed.bozo or not feed.entries:
        await msg.edit(content="❌ Could not find a YouTube channel with that ID.")
        return

    youtube_channel_name = feed.feed.get('title', 'Unknown Channel')
    is_new = add_monitor(ctx.guild.id, youtube_channel_id, youtube_channel_name, channel.id)

    await ensure_announcement_channel(channel)
    await clear_bot_messages(channel)
    monitor = {
        'youtube_channel_id': youtube_channel_id,
        'youtube_channel_name': youtube_channel_name,
        'discord_channel_id': channel.id
    }
    await post_latest_video(ctx.guild, monitor, channel, force=True)

    action = "Added" if is_new else "Updated"
    embed = discord.Embed(
        title=f"✅ Monitor {action}",
        description=f"Now monitoring **{youtube_channel_name}**\nPosting to {channel.mention}",
        color=discord.Color.green()
    )
    embed.add_field(name="YouTube ID", value=f"`{youtube_channel_id}`", inline=True)
    embed.add_field(name="Channel Type", value="📢 Announcement (auto-publish on)", inline=True)
    await msg.edit(content=None, embed=embed)

@bot.command(name='channel')
@commands.has_permissions(administrator=True)
async def cmd_channel(ctx, youtube_name: str = None, channel: discord.TextChannel = None):
    """Update the Discord channel for an existing monitor: !channel <youtube_name> <#discord-channel>"""
    if not youtube_name or not channel:
        await ctx.send("❌ Usage: `!channel <youtube_channel_name_or_id> <#discord-channel>`")
        return

    monitors = get_guild_monitors(ctx.guild.id)
    target = None
    for m in monitors:
        if m['youtube_channel_name'].lower() == youtube_name.lower() or m['youtube_channel_id'] == youtube_name:
            target = m
            break

    if not target:
        names = ", ".join(f"**{m['youtube_channel_name']}**" for m in monitors) or "none"
        await ctx.send(f"❌ No monitor named `{youtube_name}` found. Current monitors: {names}")
        return

    target['discord_channel_id'] = channel.id
    set_guild_monitors(ctx.guild.id, monitors)

    await ensure_announcement_channel(channel)
    await clear_bot_messages(channel)
    await post_latest_video(ctx.guild, target, channel, force=True)

    embed = discord.Embed(
        title="✅ Monitor Updated",
        description=f"**{target['youtube_channel_name']}** will now post to {channel.mention}",
        color=discord.Color.green()
    )
    embed.add_field(name="Channel Type", value="📢 Announcement (auto-publish on)", inline=True)
    await ctx.send(embed=embed)

@bot.command(name='postnow')
@commands.has_permissions(administrator=True)
async def cmd_postnow(ctx):
    """Manually post the latest video for all monitors"""
    monitors = get_guild_monitors(ctx.guild.id)

    if not monitors:
        await ctx.send("❌ No monitors configured. Use `!addnew <yt_id> <#channel>` to add one.")
        return

    msg = await ctx.send(f"📡 Posting latest videos for {len(monitors)} monitor(s)...")
    results = []

    for monitor in monitors:
        discord_channel = ctx.guild.get_channel(monitor['discord_channel_id'])
        if not discord_channel:
            results.append(f"❌ **{monitor['youtube_channel_name']}** — Discord channel not found")
            continue
        try:
            video = await post_latest_video(ctx.guild, monitor, discord_channel, force=True)
            if video:
                results.append(f"✅ **{monitor['youtube_channel_name']}** → {discord_channel.mention}\n↳ [{video['title']}]({video['link']})")
            else:
                results.append(f"⚠️ **{monitor['youtube_channel_name']}** — No videos found")
        except Exception as e:
            results.append(f"❌ **{monitor['youtube_channel_name']}** — Error: {e}")

    embed = discord.Embed(
        title="📡 Post Results",
        description="\n\n".join(results),
        color=discord.Color.blue()
    )
    await msg.edit(content=None, embed=embed)

@bot.command(name='monitors')
@commands.has_permissions(administrator=True)
async def cmd_monitors(ctx):
    """List and manage all YouTube monitors"""
    monitors = get_guild_monitors(ctx.guild.id)

    if not monitors:
        await ctx.send("❌ No monitors configured. Use `!addnew <yt_id> <#channel>` to add one.")
        return

    embed = discord.Embed(
        title=f"📺 Monitors for {ctx.guild.name}",
        color=discord.Color.blue(),
        description=f"{len(monitors)} monitor(s) active"
    )

    for i, monitor in enumerate(monitors):
        discord_channel = ctx.guild.get_channel(monitor['discord_channel_id'])
        channel_str = discord_channel.mention if discord_channel else "⚠️ Channel not found"
        embed.add_field(
            name=f"{i + 1}. {monitor['youtube_channel_name']}",
            value=f"YouTube ID: `{monitor['youtube_channel_id']}`\nPosts to: {channel_str}",
            inline=False
        )

    embed.set_footer(text="Use !removemonitor <name or id> to remove a monitor")
    await ctx.send(embed=embed)

@bot.command(name='removemonitor')
@commands.has_permissions(administrator=True)
async def cmd_removemonitor(ctx, *, name_or_id: str = None):
    """Remove a YouTube monitor: !removemonitor <name or youtube_id>"""
    if not name_or_id:
        await ctx.send("❌ Usage: `!removemonitor <youtube_channel_name or id>`")
        return

    monitors = get_guild_monitors(ctx.guild.id)
    target = None
    for m in monitors:
        if m['youtube_channel_id'] == name_or_id or m['youtube_channel_name'].lower() == name_or_id.lower():
            target = m
            break

    if not target:
        await ctx.send(f"❌ No monitor found for `{name_or_id}`. Use `!monitors` to see all.")
        return

    remove_monitor(ctx.guild.id, target['youtube_channel_id'])
    embed = discord.Embed(
        title="✅ Monitor Removed",
        description=f"Stopped monitoring **{target['youtube_channel_name']}**",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='youtube')
async def cmd_youtube(ctx):
    """Show YouTube monitor status"""
    monitors = get_guild_monitors(ctx.guild.id)

    if not monitors:
        await ctx.send("❌ No monitors configured. Use `!addnew` to add one.")
        return

    embed = discord.Embed(title="📺 YouTube Monitor Status", color=discord.Color.red())

    for monitor in monitors:
        yt_id = monitor['youtube_channel_id']
        discord_channel = ctx.guild.get_channel(monitor['discord_channel_id'])
        try:
            feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_id}")
            latest = feed.entries[0] if feed.entries else None
            latest_str = f"[{latest.title}]({latest.link})" if latest else "Unknown"
        except Exception:
            latest_str = "Error fetching"

        embed.add_field(
            name=monitor['youtube_channel_name'],
            value=f"Latest: {latest_str}\nPosts to: {discord_channel.mention if discord_channel else 'Not set'}",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name='status')
async def cmd_status(ctx):
    """Show bot status and all monitors"""
    monitors = get_guild_monitors(ctx.guild.id)

    embed = discord.Embed(title="🤖 Bot Status", color=discord.Color.blue())
    embed.add_field(name="📺 Monitors", value=str(len(monitors)), inline=True)
    embed.add_field(name="⏰ Check Interval", value="Every 5 minutes", inline=True)
    embed.add_field(name="📌 Auto-Pin", value="Yes (unpins old ones)", inline=True)

    for monitor in monitors:
        discord_channel = ctx.guild.get_channel(monitor['discord_channel_id'])
        embed.add_field(
            name=f"📡 {monitor['youtube_channel_name']}",
            value=f"Posts to: {discord_channel.mention if discord_channel else 'Not set'}",
            inline=False
        )

    await ctx.send(embed=embed)

@bot.command(name='reset')
@commands.has_permissions(administrator=True)
async def cmd_reset(ctx):
    """Reset all posted video tracking"""
    save_posted_videos({})
    embed = discord.Embed(
        title="✅ Videos Reset",
        description="Posted video tracking cleared. Bot will repost the latest video on next check.",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='setup')
@commands.has_permissions(administrator=True)
async def cmd_setup(ctx):
    """Create the Yonkagor Daily News role and channel"""
    guild = ctx.guild
    name = "Yonkagor Daily News"
    results = []

    role = discord.utils.get(guild.roles, name=name)
    if role:
        results.append(f"📛 Role already exists: {role.mention}")
    else:
        try:
            role = await guild.create_role(
                name=name,
                color=discord.Color.red(),
                reason="Created by !setup"
            )
            results.append(f"✅ Role created: {role.mention}")
        except discord.Forbidden:
            results.append("❌ Missing permission to create roles")
        except Exception as e:
            results.append(f"❌ Error creating role: {e}")

    channel = discord.utils.get(guild.text_channels, name=name.lower().replace(" ", "-"))
    if channel is None:
        channel = discord.utils.get(guild.text_channels, name=name)
    if channel:
        results.append(f"📢 Channel already exists: {channel.mention}")
        converted = await ensure_announcement_channel(channel)
        results.append("✅ Converted to announcement channel" if converted else "⚠️ Could not convert to announcement (missing permission)")
    else:
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(send_messages=False),
                guild.me: discord.PermissionOverwrite(send_messages=True, manage_messages=True)
            }
            if role:
                overwrites[role] = discord.PermissionOverwrite(send_messages=False, read_messages=True)
            channel = await guild.create_text_channel(
                name=name,
                overwrites=overwrites,
                topic="Daily Yonkagor News - YouTube updates posted here automatically",
                reason="Created by !setup"
            )
            results.append(f"✅ Channel created: {channel.mention}")

            converted = await ensure_announcement_channel(channel)
            results.append("✅ Set as announcement channel (auto-publish enabled)" if converted else "⚠️ Could not set as announcement (missing permission)")

            default_yt_id = 'UCwHjwUSEIwLEShZR1BjbNxg'
            add_monitor(guild.id, default_yt_id, 'DailyYonKaGorNews2', channel.id)
            results.append(f"✅ Monitor configured for DailyYonKaGorNews2")

            monitor = {'youtube_channel_id': default_yt_id, 'youtube_channel_name': 'DailyYonKaGorNews2', 'discord_channel_id': channel.id}
            await post_latest_video(guild, monitor, channel, force=True)
            results.append(f"✅ Latest video posted to {channel.mention}")
        except discord.Forbidden:
            results.append("❌ Missing permission to create channels")
        except Exception as e:
            results.append(f"❌ Error: {e}")

    embed = discord.Embed(
        title="🛠️ Setup Complete",
        description="\n".join(results),
        color=discord.Color.green()
    )
    embed.set_footer(text="Use !addnew to add more YouTube channels to monitor")
    await ctx.send(embed=embed)

@bot.command(name='help_youtube')
async def cmd_help(ctx):
    """Show all available commands"""
    embed = discord.Embed(
        title="📺 YouTube Bot Commands",
        color=discord.Color.red(),
        description="All available commands"
    )

    admin_commands = [
        ("!addnew <yt_id> #channel", "Add a new YouTube channel to monitor"),
        ("!channel <yt_name> #channel", "Update Discord channel for an existing monitor"),
        ("!postnow", "Force-post the latest video for all monitors"),
        ("!monitors", "List all monitors"),
        ("!removemonitor <name or id>", "Remove a monitor"),
        ("!setup", "Create Yonkagor Daily News role and channel"),
        ("!reset", "Reset posted video tracking"),
    ]

    user_commands = [
        ("!youtube", "Show YouTube monitor status"),
        ("!status", "Show bot status"),
        ("!help_youtube", "Show this message"),
    ]

    embed.add_field(name="👨‍💼 Admin Commands", value="", inline=False)
    for cmd, desc in admin_commands:
        embed.add_field(name=cmd, value=desc, inline=False)

    embed.add_field(name="👤 User Commands", value="", inline=False)
    for cmd, desc in user_commands:
        embed.add_field(name=cmd, value=desc, inline=False)

    embed.set_footer(text="Admin commands require administrator permissions")
    await ctx.send(embed=embed)

# ============ MAIN ============

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found in environment")
        exit(1)
    print("Starting YouTube Discord Bot (multi-monitor mode)...")
    bot.run(DISCORD_TOKEN)
