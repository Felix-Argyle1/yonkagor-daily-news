"""
Discord Bot for monitoring YouTube channels and posting new videos
Enhanced with per-server settings and automatic pinning
"""

import discord
from discord.ext import commands, tasks
from discord import app_commands
import feedparser
import os
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')

# Files to store data
POSTED_VIDEOS_FILE = 'posted_videos.json'
BOT_CONFIG_FILE = 'bot_config.json'

# Initialize bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

# ============ CONFIG MANAGEMENT ============

def load_posted_videos():
    """Load list of already posted videos"""
    if os.path.exists(POSTED_VIDEOS_FILE):
        with open(POSTED_VIDEOS_FILE, 'r') as f:
            return json.load(f)
    return []

def save_posted_videos(video_ids):
    """Save list of posted videos"""
    with open(POSTED_VIDEOS_FILE, 'w') as f:
        json.dump(video_ids, f)

def load_config():
    """Load bot configuration"""
    if os.path.exists(BOT_CONFIG_FILE):
        with open(BOT_CONFIG_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_config(config):
    """Save bot configuration"""
    with open(BOT_CONFIG_FILE, 'w') as f:
        json.dump(config, f, indent=2)

def get_guild_config(guild_id):
    """Get configuration for a specific guild"""
    config = load_config()
    return config.get(str(guild_id), {
        'notification_channel': None,
        'youtube_channel_id': 'UCwHjwUSEIwLEShZR1BjbNxg',  # Default: DailyYonKaGorNews2
        'youtube_channel_name': 'DailyYonKaGorNews2',
        'last_posted_message_id': None
    })

def set_guild_config(guild_id, config_dict):
    """Save configuration for a specific guild"""
    config = load_config()
    config[str(guild_id)] = config_dict
    save_config(config)

def get_notification_channel(guild):
    """Get the configured notification channel for a guild"""
    guild_config = get_guild_config(guild.id)
    channel_id = guild_config.get('notification_channel')
    
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel and channel.permissions_for(guild.me).send_messages:
            return channel
    
    return None

def extract_video_id(yt_url):
    """Extract video ID from YouTube URL"""
    if 'watch?v=' in yt_url:
        return yt_url.split('watch?v=')[1].split('&')[0]
    return None

# ============ EVENTS ============

@bot.event
async def on_ready():
    try:
        synced = await bot.tree.sync()
        print(f'Synced {len(synced)} command(s)')
    except Exception as e:
        print(f"Failed to sync commands: {e}")
    
    print(f'{bot.user} has connected to Discord!')
    check_youtube.start()
    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name="YouTube uploads"
        )
    )
    print("Bot is ready!")

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message("❌ You don't have permission to use this command.", ephemeral=True)
    else:
        await interaction.response.send_message(f"❌ An error occurred: {error}", ephemeral=True)

# ============ YOUTUBE CHECK TASK ============

@tasks.loop(minutes=5)  # Check every 5 minutes
async def check_youtube():
    """Periodically check YouTube channel for new videos"""
    try:
        # Check each guild's configured YouTube channel
        for guild in bot.guilds:
            guild_config = get_guild_config(guild.id)
            channel_id = guild_config.get('youtube_channel_id')
            channel_name = guild_config.get('youtube_channel_name', 'Unknown')
            notification_channel = get_notification_channel(guild)
            
            if not notification_channel:
                continue  # Skip if no channel configured
            
            if not channel_id:
                continue  # Skip if no YouTube channel configured
            
            # Build RSS feed URL
            rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
            
            try:
                # Parse the RSS feed
                feed = feedparser.parse(rss_url)
                
                if feed.bozo:
                    print(f"Warning: Feed parsing warning - {feed.bozo_exception}")
                
                posted_videos = load_posted_videos()
                
                # Get the latest video
                latest_video = None
                for entry in feed.entries:
                    if entry.yt_videoid not in posted_videos:
                        latest_video = {
                            'id': entry.yt_videoid,
                            'title': entry.title,
                            'link': entry.link,
                            'published': entry.published,
                            'author': entry.author
                        }
                        break
                
                if latest_video:
                    # Unpin old messages from this bot
                    try:
                        pins = await notification_channel.pins()
                        for pin_msg in pins:
                            if pin_msg.author == bot.user:
                                await pin_msg.unpin()
                                print(f"Unpinned old message in {guild.name}")
                    except Exception as e:
                        print(f"Error unpinning: {e}")
                    
                    # Create and send embed
                    embed = discord.Embed(
                        title=latest_video['title'],
                        url=latest_video['link'],
                        color=discord.Color.red(),  # YouTube red
                        description=f"New video from {latest_video['author']}"
                    )
                    embed.set_thumbnail(
                        url=f"https://i.ytimg.com/vi/{latest_video['id']}/maxresdefault.jpg"
                    )
                    embed.add_field(
                        name="Channel",
                        value=latest_video['author'],
                        inline=True
                    )
                    embed.add_field(
                        name="Posted",
                        value=f"<t:{int(datetime.fromisoformat(latest_video['published'].replace('Z', '+00:00')).timestamp())}:R>",
                        inline=True
                    )
                    embed.set_footer(text=f"Monitoring: {channel_name}")
                    
                    try:
                        message = await notification_channel.send(embed=embed)
                        
                        # Pin the message
                        try:
                            await message.pin()
                            print(f"Pinned video: {latest_video['title']} in {guild.name}")
                        except discord.Forbidden:
                            print(f"No permission to pin in {notification_channel.name}")
                        except Exception as e:
                            print(f"Error pinning message: {e}")
                        
                        # Update posted videos
                        posted_videos.append(latest_video['id'])
                        save_posted_videos(posted_videos)
                        
                        print(f"Posted video: {latest_video['title']} to {guild.name}")
                    except discord.Forbidden:
                        print(f"No permission to post in {notification_channel.name}")
                    except Exception as e:
                        print(f"Error posting video: {e}")
            
            except Exception as e:
                print(f"Error checking YouTube for {guild.name}: {e}")
    
    except Exception as e:
        print(f"Error in check_youtube task: {e}")

# ============ SLASH COMMANDS ============

@bot.tree.command(name="channel", description="Set the notification channel for YouTube videos")
@app_commands.describe(channel="The channel where videos will be posted")
@app_commands.checks.has_permissions(administrator=True)
async def slash_channel(interaction: discord.Interaction, channel: discord.TextChannel):
    """Set the notification channel for YouTube videos"""
    guild_config = get_guild_config(interaction.guild.id)
    guild_config['notification_channel'] = channel.id
    set_guild_config(interaction.guild.id, guild_config)
    
    embed = discord.Embed(
        title="✅ Channel Updated",
        description=f"YouTube videos will now be posted to {channel.mention}",
        color=discord.Color.green()
    )
    embed.add_field(name="Current Youtuber", value=guild_config.get('youtube_channel_name', 'Not set'), inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="youtuber", description="Set which YouTube channel to monitor")
@app_commands.describe(channel_id="YouTube channel ID (starts with UC, 24 characters)")
@app_commands.checks.has_permissions(administrator=True)
async def slash_youtuber(interaction: discord.Interaction, channel_id: str):
    """Set the YouTube channel to monitor"""
    
    channel_id = channel_id.strip()
    
    # Check for URL instead of ID
    if 'youtube.com' in channel_id or 'youtu.be' in channel_id:
        if '@' in channel_id:
            channel_name = channel_id.split('@')[1].split('/')[0]
            embed = discord.Embed(
                title="⚠️ Channel Handle Detected",
                description=f"Please provide the channel ID instead of the handle.",
                color=discord.Color.orange()
            )
            embed.add_field(
                name="How to find Channel ID",
                value="1. Go to the channel\n2. Look for the URL pattern: `/c/UCxxxxxxxx`\n3. Or use an online lookup tool",
                inline=False
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return
        else:
            await interaction.response.send_message("❌ Please provide just the Channel ID (starting with UC), not the URL.", ephemeral=True)
            return
    
    # Validate channel ID format
    if not channel_id.startswith('UC') or len(channel_id) != 24:
        embed = discord.Embed(
            title="❌ Invalid Channel ID",
            description=f"`{channel_id}` is not a valid YouTube channel ID.",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Valid format",
            value="Channel IDs start with `UC` and are 24 characters long\nExample: `UCwHjwUSEIwLEShZR1BjbNxg`",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Test if the channel exists
    test_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    feed = feedparser.parse(test_url)
    
    if feed.bozo or not feed.entries:
        embed = discord.Embed(
            title="❌ Channel Not Found",
            description=f"Could not find a YouTube channel with ID: `{channel_id}`",
            color=discord.Color.red()
        )
        embed.add_field(
            name="Possible reasons",
            value="• Invalid channel ID\n• Channel is private/deleted\n• Channel ID format is wrong",
            inline=False
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)
        return
    
    # Get channel name from feed
    channel_name = feed.feed.get('title', 'Unknown Channel')
    
    guild_config = get_guild_config(interaction.guild.id)
    guild_config['youtube_channel_id'] = channel_id
    guild_config['youtube_channel_name'] = channel_name
    set_guild_config(interaction.guild.id, guild_config)
    
    embed = discord.Embed(
        title="✅ YouTuber Updated",
        description=f"Now monitoring: **{channel_name}**",
        color=discord.Color.green()
    )
    embed.add_field(name="Channel ID", value=f"`{channel_id}`", inline=True)
    notify_ch = interaction.guild.get_channel(guild_config.get('notification_channel'))
    embed.add_field(name="Posts to", value=notify_ch.mention if notify_ch else "Not set", inline=True)
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="profile", description="Show the bot's profile settings for this server")
@app_commands.checks.has_permissions(administrator=True)
async def slash_profile(interaction: discord.Interaction):
    """Show the bot's profile settings for this server"""
    guild_config = get_guild_config(interaction.guild.id)
    
    channel_id = guild_config.get('notification_channel')
    channel = interaction.guild.get_channel(channel_id) if channel_id else None
    
    youtube_channel = guild_config.get('youtube_channel_name', 'Not configured')
    youtube_id = guild_config.get('youtube_channel_id', 'Not set')
    
    embed = discord.Embed(
        title=f"🤖 Bot Profile for {interaction.guild.name}",
        color=discord.Color.blue()
    )
    embed.add_field(
        name="📺 YouTube Channel",
        value=f"{youtube_channel}\n`{youtube_id}`",
        inline=False
    )
    embed.add_field(
        name="📢 Notification Channel",
        value=channel.mention if channel else "Not set",
        inline=False
    )
    embed.add_field(
        name="⏰ Check Interval",
        value="Every 5 minutes",
        inline=True
    )
    embed.add_field(
        name="📌 Auto-Pin",
        value="Enabled (pins latest, unpins old)",
        inline=True
    )
    embed.set_footer(text="Use /channel and /youtuber commands to update settings")
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="youtube", description="Manually check for new YouTube videos")
async def slash_youtube(interaction: discord.Interaction):
    """Manually check for new YouTube videos"""
    await interaction.response.defer()
    
    guild_config = get_guild_config(interaction.guild.id)
    channel_id = guild_config.get('youtube_channel_id')
    channel_name = guild_config.get('youtube_channel_name', 'Not configured')
    
    if not channel_id:
        await interaction.followup.send("❌ No YouTube channel configured. Use `/youtuber` to set one.")
        return
    
    try:
        rss_url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
        feed = feedparser.parse(rss_url)
        posted_videos = load_posted_videos()
        
        new_count = sum(1 for entry in feed.entries if entry.yt_videoid not in posted_videos)
        latest = feed.entries[0] if feed.entries else None
        
        embed = discord.Embed(
            title="📺 YouTube Channel Status",
            description=f"Monitoring: **{channel_name}**",
            color=discord.Color.red()
        )
        embed.add_field(name="New Videos Found", value=str(new_count), inline=True)
        embed.add_field(name="Total Posted", value=str(len(posted_videos)), inline=True)
        if latest:
            embed.add_field(name="Latest Video", value=f"[{latest.title}]({latest.link})", inline=False)
        
        await interaction.followup.send(embed=embed)
    except Exception as e:
        await interaction.followup.send(f"❌ Error checking YouTube: {e}")

@bot.tree.command(name="status", description="Show bot status and settings")
async def slash_status(interaction: discord.Interaction):
    """Show bot status and settings"""
    guild_config = get_guild_config(interaction.guild.id)
    
    channel_id = guild_config.get('notification_channel')
    channel = interaction.guild.get_channel(channel_id) if channel_id else None
    
    youtube_channel = guild_config.get('youtube_channel_name', 'Not configured')
    posted_videos = load_posted_videos()
    
    try:
        yt_id = guild_config.get('youtube_channel_id')
        feed = feedparser.parse(f"https://www.youtube.com/feeds/videos.xml?channel_id={yt_id}")
        new_count = sum(1 for entry in feed.entries if entry.yt_videoid not in posted_videos)
    except:
        new_count = "Unknown"
    
    embed = discord.Embed(
        title="🤖 Bot Status",
        color=discord.Color.blue()
    )
    embed.add_field(name="📺 Monitoring", value=youtube_channel, inline=True)
    embed.add_field(name="📢 Posts to", value=channel.mention if channel else "Not set", inline=True)
    embed.add_field(name="📬 New Videos", value=str(new_count), inline=True)
    embed.add_field(name="✅ Total Posted", value=str(len(posted_videos)), inline=True)
    embed.add_field(name="⏰ Check Interval", value="Every 5 minutes", inline=True)
    embed.add_field(name="📌 Auto-Pin", value="Yes (unpins old ones)", inline=True)
    
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="reset", description="Reset the posted videos list (Admin only)")
@app_commands.checks.has_permissions(administrator=True)
async def slash_reset(interaction: discord.Interaction):
    """Reset the posted videos list"""
    save_posted_videos([])
    embed = discord.Embed(
        title="✅ Videos Reset",
        description="Posted videos list has been cleared. Bot will now repost old videos.",
        color=discord.Color.green()
    )
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="help", description="Show all available commands")
async def slash_help(interaction: discord.Interaction):
    """Show all available commands"""
    embed = discord.Embed(
        title="📺 YouTube Bot Commands",
        color=discord.Color.red(),
        description="All available commands"
    )
    
    admin_commands = [
        ("/channel #channel", "Set the channel for video notifications"),
        ("/youtuber <channel_id>", "Set which YouTube channel to monitor"),
        ("/reset", "Reset posted videos list"),
        ("/profile", "Show bot settings for this server"),
    ]
    
    user_commands = [
        ("/youtube", "Manually check for new videos"),
        ("/status", "Show bot status"),
        ("/help", "Show this message"),
    ]
    
    embed.add_field(name="👨‍💼 Admin Commands", value="", inline=False)
    for cmd, desc in admin_commands:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    embed.add_field(name="👤 User Commands", value="", inline=False)
    for cmd, desc in user_commands:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    embed.set_footer(text="Admin commands require administrator permissions")
    await interaction.response.send_message(embed=embed)

# ============ MAIN ============

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env file")
        print("Please create a .env file with your Discord bot token")
        exit(1)
    
    print("Starting YouTube Discord Bot...")
    print("Default monitoring: DailyYonKaGorNews2")
    
    bot.run(DISCORD_TOKEN)
