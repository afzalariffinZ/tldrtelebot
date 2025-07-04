# bot.py

import os
import logging
from datetime import datetime, timezone

# For loading environment variables from .env file
from dotenv import load_dotenv

# Google Gemini AI
import google.generativeai as genai

# Telegram Bot Library
from telegram import Update, BotCommand
from telegram.helpers import escape_markdown
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
    PicklePersistence,
    ApplicationBuilder
)

# --- Configuration ---
# Load environment variables from .env file
load_dotenv()

# Set up logging to see bot's activity
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Get API keys from environment variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

# Check if keys are set
if not TELEGRAM_TOKEN or not GEMINI_API_KEY:
    raise ValueError("TELEGRAM_TOKEN and GEMINI_API_KEY must be set in the .env file.")

# Configure the Gemini API
genai.configure(api_key=GEMINI_API_KEY)
# Using a specific model known for good text generation
gemini_model = genai.GenerativeModel('gemini-1.5-flash-latest')

# --- Bot State and Constants ---
# This dictionary will hold message buffers for each chat
# Structure: {chat_id: [(timestamp, author, text), ...]}
# We use a dictionary in bot_data provided by PicklePersistence for persistence
MESSAGE_BUFFER_KEY = "message_buffer"
MAX_BUFFER_SIZE = 150  # Max messages to store before summarizing

# --- Bot Command Handlers ---

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends a welcome message when the /start command is issued."""
    # Initialize the message buffer for this chat if it doesn't exist
    context.bot_data.setdefault(MESSAGE_BUFFER_KEY, {})
    context.bot_data[MESSAGE_BUFFER_KEY].setdefault(update.effective_chat.id, [])
    
    await update.message.reply_text(
        "👋 Hello! I'm your TLDR Bot.\n\n"
        "I'll listen to the conversation in this group. "
        "When you want a summary, just type `/tldr`.\n\n"
        "Please make sure I have permission to read messages!"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sends help information when the /help command is issued."""
    await update.message.reply_text(
        "🤖 **How to use me:**\n\n"
        "1. I automatically store messages sent in this group.\n"
        "2. When you're ready for a summary of the recent conversation, type `/tldr`.\n"
        "3. I'll send the conversation to an AI to get a concise summary.\n"
        "4. After summarizing, I'll clear my memory to start fresh for the next one."
    )

async def store_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stores a message from the group chat."""
    chat_id = update.effective_chat.id
    user = update.effective_user
    text = update.message.text

    # Ignore empty messages
    if not text:
        return

    # Initialize buffer for the chat if it's new
    context.bot_data.setdefault(MESSAGE_BUFFER_KEY, {})
    if chat_id not in context.bot_data[MESSAGE_BUFFER_KEY]:
        context.bot_data[MESSAGE_BUFFER_KEY][chat_id] = []
        logger.info(f"Initialized new message buffer for chat_id: {chat_id}")

    # Prepare message details
    author = user.first_name or user.username
    timestamp = datetime.now(timezone.utc)
    
    # Add message to the buffer
    message_data = (timestamp, author, text)
    context.bot_data[MESSAGE_BUFFER_KEY][chat_id].append(message_data)
    logger.info(f"Stored message from {author} in chat {chat_id}")

    # Keep the buffer from growing too large
    while len(context.bot_data[MESSAGE_BUFFER_KEY][chat_id]) > MAX_BUFFER_SIZE:
        context.bot_data[MESSAGE_BUFFER_KEY][chat_id].pop(0) # Remove the oldest message

async def tldr_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generates and sends a TLDR summary."""
    chat_id = update.effective_chat.id
    message_buffer = context.bot_data.get(MESSAGE_BUFFER_KEY, {}).get(chat_id, [])

    if len(message_buffer) < 3:
        await update.message.reply_text("There aren't enough messages to summarize yet. Keep chatting!")
        return

    processing_message = await update.message.reply_text(f"🧠 Got it. Summarizing the last {len(message_buffer)} messages...")

    conversation = "\n".join([f"{author}: {text}" for _, author, text in message_buffer])
    
    prompt = f"""
    You are a helpful assistant in a Telegram group chat. Your task is to provide a concise summary (a TL;DR) of the following conversation.
    The summary should be clear, easy to read, and presented in a few bullet points using markdown dashes (-) or asterisks (*).
    Do not add any extra commentary before or after the summary. Just provide the bullet points.

    Here is the chat history:
    ---
    {conversation}
    ---
    """

    try:
        response = await gemini_model.generate_content_async(prompt)
        summary = response.text

        # --- CHANGE 1: ESCAPE THE AI-GENERATED SUMMARY ---
        # This will protect against any special markdown characters in the AI's response.
        # We use version=2 because it's the modern standard for Telegram.
        escaped_summary = escape_markdown(summary, version=2)

        # --- CHANGE 2: USE THE ESCAPED SUMMARY AND PARSE_MODE='MarkdownV2' ---
        # Note: We now use 'MarkdownV2' which is stricter and works with the escape_markdown helper.
        # Our own manually-added formatting (like the bold title) is safe because we typed it correctly.
        final_text = f"**📜 TL;DR of the last {len(message_buffer)} messages:**\n\n{escaped_summary}"
        
        await context.bot.send_message(
            chat_id=chat_id,
            text=final_text,
            parse_mode='MarkdownV2' # Using the more modern and reliable MarkdownV2
        )

        context.bot_data[MESSAGE_BUFFER_KEY][chat_id] = []
        logger.info(f"Successfully summarized and cleared buffer for chat {chat_id}")

    except Exception as e:
        logger.error(f"Error generating summary for chat {chat_id}: {e}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="😥 Sorry, I ran into an error while trying to create the summary. Please try again later."
        )
    finally:
        await processing_message.delete()

async def post_init(application: Application):
    """Set the bot's commands after initialization."""
    await application.bot.set_my_commands([
        BotCommand("start", "Start the bot and get a welcome message"),
        BotCommand("tldr", "Summarize the recent conversation"),
        BotCommand("help", "Show help information")
    ])

# --- Main Bot Execution ---

def main():
    """Start the bot."""
     # --- MODIFICATION FOR RENDER ---
    # On-Demand disks are mounted at a specific path, e.g., /var/data
    # We get this path from an environment variable for flexibility.
    persistence_path = os.path.join(
        os.getenv("RENDER_DISK_MOUNT_PATH", "."), # Default to current dir if var not set
        "tldr_bot_data.pkl"
    )
    print(f"Using persistence file at: {persistence_path}") # Good for debugging
    persistence = PicklePersistence(filepath=persistence_path)
    # --- END MODIFICATION ---

    # Create the Application and pass it your bot's token.
    application = (
        ApplicationBuilder()
        .token(TELEGRAM_TOKEN)
        .persistence(persistence)
        .post_init(post_init)
        .build()
    )

    # Add command handlers
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("tldr", tldr_command))
    
    # Add a message handler to store all non-command text messages
    # The `& (~filters.COMMAND)` part ensures we don't store commands themselves
    application.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), store_message))
    
    # Run the bot until the user presses Ctrl-C
    logger.info("Bot is starting... Press Ctrl-C to stop.")
    application.run_polling()


if __name__ == '__main__':
    main()
