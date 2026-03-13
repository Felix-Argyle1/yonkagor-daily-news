"""
Discord Bot for monitoring a YouTube channel and posting new videos
Monitors @DailyYonKaGorNews2 channel for new uploads
Enhanced with pinning and channel selection features
"""

import discord
from discord.ext import commands, tasks
import feedparser
import os
import json
from datetime import datetime
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configuration
DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
YOUTUBE_CHANNEL_URL = "https://www.youtube.com/@DailyYonKaGorNews2"
YOUTUBE_CHANNEL_ID = "UCwHjwUSEIwLEShZR1BjbNxg"
RSS_FEED_URL = f"https://www.youtube.com/feeds/videos.xml?channel_id={YOUTUBE_CHANNEL_ID}"

# Files to store data
POSTED_VIDEOS_FILE = 'posted_videos.json'
BOT_CONFIG_FILE = 'bot_config.json'
MAX_PINNED_VIDEOS = 5  # Maximum videos to keep pinned

# Initialize bot
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

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

def get_notification_channel(guild):
    """Get the configured notification channel for a guild"""
    config = load_config()
    guild_config = config.get(str(guild.id), {})
    channel_id = guild_config.get('notification_channel')
    
    if channel_id:
        channel = guild.get_channel(channel_id)
        if channel and channel.permissions_for(guild.me).send_messages:
            return channel
    
    # Fallback: find any available channel
    for ch in guild.text_channels:
        if any(name in ch.name.lower() for name in ['general', 'notifications', 'youtube', 'videos']):
            if ch.permissions_for(guild.me).send_messages:
                return ch
    
    for ch in guild.text_channels:
        if ch.permissions_for(guild.me).send_messages:
            return ch
    
    return None

def get_pinned_videos(guild):
    """Get list of pinned video IDs for this guild"""
    config = load_config()
    guild_config = config.get(str(guild.id), {})
    return guild_config.get('pinned_videos', [])

def save_pinned_videos(guild, video_ids):
    """Save pinned videos for this guild"""
    config = load_config()
    if str(guild.id) not in config:
        config[str(guild.id)] = {}
    config[str(guild.id)]['pinned_videos'] = video_ids
    save_config(config)

def extract_video_id(yt_url):
    """Extract video ID from YouTube URL"""
    if 'watch?v=' in yt_url:
        return yt_url.split('watch?v=')[1].split('&')[0]
    return None

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

@tasks.loop(minutes=5)  # Check every 5 minutes
async def check_youtube():
    """Periodically check YouTube channel for new videos"""
    try:
        # Parse the RSS feed
        feed = feedparser.parse(RSS_FEED_URL)
        
        if feed.bozo:
            print(f"Warning: Feed parsing warning - {feed.bozo_exception}")
        
        posted_videos = load_posted_videos()
        new_videos = []
        
        # Get the latest entries (videos)
        for entry in feed.entries[:10]:  # Check last 10 videos
            video_id = entry.yt_videoid
            
            # If we haven't posted this video yet
            if video_id not in posted_videos:
                new_videos.append({
                    'id': video_id,
                    'title': entry.title,
                    'link': entry.link,
                    'published': entry.published,
                    'author': entry.author
                })
        
        # Post new videos to all configured channels
        if new_videos:
            for guild in bot.guilds:
                channel = get_notification_channel(guild)
                
                if channel:
                    for video in reversed(new_videos):  # Post oldest first
                        embed = discord.Embed(
                            title=video['title'],
                            url=video['link'],
                            color=discord.Color.red(),  # YouTube red
                            description=f"New video from {video['author']}"
                        )
                        embed.set_thumbnail(
                            url=f"https://i.ytimg.com/vi/{video['id']}/maxresdefault.jpg"
                        )
                        embed.add_field(
                            name="Channel",
                            value=video['author'],
                            inline=True
                        )
                        embed.add_field(
                            name="Posted",
                            value=f"<t:{int(datetime.fromisoformat(video['published'].replace('Z', '+00:00')).timestamp())}:R>",
                            inline=True
                        )
                        embed.set_footer(text="DailyYonKaGorNews2 Monitor")
                        
                        try:
                            message = await channel.send(embed=embed)
                            
                            # Pin the message
                            try:
                                await message.pin()
                                print(f"Pinned video: {video['title']} in {guild.name}")
                                
                                # Manage pinned videos list
                                pinned = get_pinned_videos(guild)
                                pinned.append(video['id'])
                                
                                # If we have too many pinned, unpin the oldest
                                if len(pinned) > MAX_PINNED_VIDEOS:
                                    old_video_id = pinned.pop(0)
                                    # Try to unpin old videos
                                    try:
                                        pins = await channel.pins()
                                        for pin_msg in pins:
                                            if hasattr(pin_msg.embeds[0], 'thumbnail') and old_video_id in str(pin_msg.embeds[0].thumbnail.url):
                                                await pin_msg.unpin()
                                                print(f"Unpinned old video: {old_video_id}")
                                                break
                                    except:
                                        pass
                                
                                save_pinned_videos(guild, pinned)
                            except discord.Forbidden:
                                print(f"No permission to pin in {channel.name}")
                            except Exception as e:
                                print(f"Error pinning message: {e}")
                            
                            print(f"Posted video: {video['title']} to {guild.name}")
                        except discord.Forbidden:
                            print(f"No permission to post in {channel.name}")
                        except Exception as e:
                            print(f"Error posting video: {e}")
            
            # Update posted videos list
            posted_videos.extend([v['id'] for v in new_videos])
            save_posted_videos(posted_videos)
    
    except Exception as e:
        print(f"Error checking YouTube: {e}")

@bot.command(name='youtube', help='Check for new YouTube videos manually')
async def manual_check(ctx):
    """Manual command to check for new videos"""
    await ctx.send("Checking YouTube channel...")
    try:
        feed = feedparser.parse(RSS_FEED_URL)
        posted_videos = load_posted_videos()
        
        new_count = sum(1 for entry in feed.entries if entry.yt_videoid not in posted_videos)
        
        embed = discord.Embed(
            title="YouTube Channel Status",
            description=f"Monitoring: DailyYonKaGorNews2",
            color=discord.Color.red()
        )
        embed.add_field(name="New Videos Found", value=str(new_count), inline=True)
        embed.add_field(name="Videos Tracked", value=str(len(posted_videos)), inline=True)
        embed.add_field(name="Latest Video", value=feed.entries[0].title if feed.entries else "None", inline=False)
        
        await ctx.send(embed=embed)
    except Exception as e:
        await ctx.send(f"Error checking YouTube: {e}")

@bot.command(name='channel', help='Set the notification channel for YouTube videos')
@commands.has_permissions(administrator=True)
async def set_channel(ctx, channel: discord.TextChannel = None):
    """Set the channel where videos will be posted"""
    if channel is None:
        channel = ctx.channel
    
    config = load_config()
    if str(ctx.guild.id) not in config:
        config[str(ctx.guild.id)] = {}
    
    config[str(ctx.guild.id)]['notification_channel'] = channel.id
    save_config(config)
    
    embed = discord.Embed(
        title="✅ Channel Updated",
        description=f"YouTube videos will now be posted to {channel.mention}",
        color=discord.Color.green()
    )
    await ctx.send(embed=embed)

@bot.command(name='status', help='Show bot status and settings')
async def status(ctx):
    """Show bot status"""
    config = load_config()
    guild_config = config.get(str(ctx.guild.id), {})
    channel_id = guild_config.get('notification_channel')
    channel = ctx.guild.get_channel(channel_id) if channel_id else None
    
    posted_videos = load_posted_videos()
    pinned = get_pinned_videos(ctx.guild)
    
    try:
        feed = feedparser.parse(RSS_FEED_URL)
        new_count = sum(1 for entry in feed.entries if entry.yt_videoid not in posted_videos)
    except:
        new_count = "Unknown"
    
    embed = discord.Embed(
        title="🤖 YouTube Bot Status",
        color=discord.Color.blue()
    )
    embed.add_field(name="Channel", value=channel.mention if channel else "Not set (using default)", inline=False)
    embed.add_field(name="New Videos", value=str(new_count), inline=True)
    embed.add_field(name="Total Posted", value=str(len(posted_videos)), inline=True)
    embed.add_field(name="Pinned Videos", value=str(len(pinned)), inline=True)
    embed.add_field(name="Check Interval", value="Every 5 minutes", inline=True)
    embed.set_footer(text="Monitoring: DailyYonKaGorNews2")
    
    await ctx.send(embed=embed)

@bot.command(name='reset', help='Reset the posted videos list')
@commands.has_permissions(administrator=True)
async def reset_videos(ctx):
    """Reset posted videos (admin only)"""
    save_posted_videos([])
    save_pinned_videos(ctx.guild, [])
    await ctx.send("✅ Posted videos list has been reset!")

@bot.command(name='help_youtube', help='Show all available commands')
async def help_youtube(ctx):
    """Show all YouTube bot commands"""
    embed = discord.Embed(
        title="📺 YouTube Bot Commands",
        color=discord.Color.red(),
        description="All available commands for the YouTube bot"
    )
    
    commands_list = [
        ("!youtube", "Manually check for new videos"),
        ("!channel #channel", "Set the channel for video notifications (Admin)"),
        ("!status", "Show bot status and statistics"),
        ("!reset", "Reset posted videos list (Admin)"),
        ("!help_youtube", "Show this message"),
    ]
    
    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)
    
    embed.set_footer(text="Admin commands require administrator permissions")
    await ctx.send(embed=embed)

# Error handling
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("You don't have permission to use this command.")
    else:
        await ctx.send(f"An error occurred: {error}")

if __name__ == "__main__":
    if not DISCORD_TOKEN:
        print("ERROR: DISCORD_TOKEN not found in .env file")
        print("Please create a .env file with your Discord bot token")
        exit(1)
    
    # Update the RSS feed URL with the correct channel ID
    print("Starting YouTube Discord Bot...")
    print(f"Monitoring: {YOUTUBE_CHANNEL_URL}")
    
    bot.run(DISCORD_TOKEN)