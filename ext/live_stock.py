"""
Live Stock Manager
Author: fdyytu
Created at: 2025-03-07 18:30:16 UTC
Last Modified: 2025-03-15 16:33:10 UTC

Dependencies:
- ext.product_manager: For product operations
- ext.balance_manager: For balance operations
- ext.trx: For transaction operations
- ext.admin_service: For maintenance mode
- ext.constants: For configuration and responses
"""

import discord
from discord.ext import commands, tasks
import logging
import asyncio
from datetime import datetime
from typing import Optional, Dict
from discord import ui

from .constants import (
    COLORS,
    MESSAGES,
    UPDATE_INTERVAL,
    CACHE_TIMEOUT,
    Stock,
    Status,
    CURRENCY_RATES,
    COG_LOADED
)
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager
from .product_manager import ProductManagerService
from .balance_manager import BalanceManagerService 
from .trx import TransactionManager
from .admin_service import AdminService

class LiveStockManager(BaseLockHandler):
    def __init__(self, bot):
        if not hasattr(self, 'initialized') or not self.initialized:
            super().__init__()
            self.bot = bot
            self.logger = logging.getLogger("LiveStockManager")
            self.cache_manager = CacheManager()

            # Initialize services
            self.product_service = ProductManagerService(bot)
            self.balance_service = BalanceManagerService(bot)
            self.trx_manager = TransactionManager(bot)
            self.admin_service = AdminService(bot)

            # Channel configuration
            self.stock_channel_id = int(self.bot.config.get('id_live_stock', 0))
            self.current_stock_message = None
            self.button_manager = None
            self._ready = asyncio.Event()
            self.initialized = True
            self.logger.info("LiveStockManager initialized")

    async def initialize(self):
        """Initialize manager and set ready state"""
        if not self._ready.is_set():
            self._ready.set()
            self.logger.info("LiveStockManager is ready")

    async def find_last_message(self) -> Optional[discord.Message]:
        """Cari pesan terakhir yang dikirim bot di channel"""
        try:
            channel = self.bot.get_channel(self.stock_channel_id)
            if not channel:
                return None
                
            # Cari pesan terakhir dari bot di channel
            async for message in channel.history(limit=50):  # Batasi 50 pesan terakhir
                if (message.author == self.bot.user and 
                    len(message.embeds) > 0 and 
                    message.embeds[0].title and 
                    "Growtopia Shop Status" in message.embeds[0].title):
                    return message
            return None
        except Exception as e:
            self.logger.error(f"Error finding last message: {e}")
            return None

    async def set_button_manager(self, button_manager):
        """Set button manager untuk integrasi"""
        self.button_manager = button_manager
        self.logger.info("Button manager set successfully")

    async def create_stock_embed(self) -> discord.Embed:
        """Buat embed untuk display stock dengan tema modern"""
        try:
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                return discord.Embed(
                    title="🔧 Sistem dalam Maintenance",
                    description=MESSAGES.INFO['MAINTENANCE'],
                    color=COLORS.WARNING,
                    timestamp=datetime.utcnow()
                )

            # Get products dari ProductManager dengan proper response handling
            cache_key = 'all_products_display'
            cached_products = await self.cache_manager.get(cache_key)

            if not cached_products:
                product_response = await self.product_service.get_all_products()
                if not product_response.success:
                    raise ValueError(product_response.error)
                products = product_response.data
                await self.cache_manager.set(
                    cache_key,
                    products,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                )
            else:
                products = cached_products

            # Create modern embed with dark theme
            embed = discord.Embed(
                title="🏪 Growtopia Shop Status",
                description=(
                    "```ansi\n"
                    "\u001b[0;37mSelamat datang di \u001b[0;33mGrowtopia Shop\u001b[0m!\n"
                    "\u001b[0;90mReal-time stock monitoring system\u001b[0m\n"
                    "```"
                ),
                color=discord.Color.from_rgb(32, 34, 37)  # Discord dark theme color
            )

            # Server time dengan format modern
            current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
            embed.add_field(
                name="⏰ Server Time",
                value=f"```ansi\n\u001b[0;36m{current_time} UTC\u001b[0m```",
                inline=False
            )

            try:
                # Grouping products by category
                categories = {}
                for product in products:
                    category = product.get('category', 'Other')
                    if category not in categories:
                        categories[category] = []
                    categories[category].append(product)

                for category, category_products in categories.items():
                    # Category header dengan styling
                    category_header = f"\n__**{category}**__\n"
                    category_items = []

                    for product in category_products:
                        try:
                            # Get stock count dengan caching
                            try:
                                product_code = product.get('code')
                                if not product_code:
                                    self.logger.error(f"Product code not found in data: {str(product)}")
                                    continue

                                stock_cache_key = f'stock_count_{product_code}'
                                stock_count = await self.cache_manager.get(stock_cache_key)
                            except Exception as e:
                                self.logger.error(f"Error accessing product code: {str(e)} for product: {str(product)}")
                                continue

                            if stock_count is None:
                                stock_response = await self.product_service.get_stock_count(product_code)
                                if not stock_response.success:
                                    continue
                                stock_count = stock_response.data
                                await self.cache_manager.set(
                                    stock_cache_key,
                                    stock_count,
                                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                                )

                            # Status indicators dengan warna
                            if stock_count > Stock.ALERT_THRESHOLD:
                                status_color = "32"  # Green
                                status_emoji = "🟢"
                            elif stock_count > 0:
                                status_color = "33"  # Yellow
                                status_emoji = "🟡"
                            else:
                                status_color = "31"  # Red
                                status_emoji = "🔴"

                            # Format price menggunakan currency rates
                            price = float(product['price'])
                            price_display = self._format_price(price)

                            # Product display dengan ANSI formatting
                            product_info = (
                                f"```ansi\n"
                                f"{status_emoji} \u001b[0;{status_color}m{product['name']}\u001b[0m\n"
                                f"└─ Price : {price_display}\n"
                                f"└─ Stock : {stock_count} unit\n"
                            )

                            if product.get('description'):
                                product_info += f"└─ Info  : {product['description']}\n"

                            product_info += "```"
                            category_items.append(product_info)

                        except Exception as e:
                            self.logger.error(f"Error processing product {product.get('name', 'Unknown')}: {e}")
                            continue

                    if category_items:
                        items_text = "\n".join(category_items)
                        embed.add_field(
                            name=category_header,
                            value=items_text,
                            inline=False
                        )

            except Exception as e:
                self.logger.error(f"Error processing categories: {e}")
                raise

            # Footer dengan update info
            embed.set_footer(
                text=f"Auto-update every {int(UPDATE_INTERVAL.LIVE_STOCK)} seconds • Last Update"
            )
            embed.timestamp = datetime.utcnow()

            return embed

        except Exception as e:
            self.logger.error(f"Error creating stock embed: {e}")
            return discord.Embed(
                title="❌ System Error",
                description=MESSAGES.ERROR['DISPLAY_ERROR'],
                color=COLORS.ERROR
            )

    def _format_price(self, price: float) -> str:
        """Format price dengan currency rates dari constants"""
        try:
            if not isinstance(price, (int, float)):
                raise ValueError(f"Invalid price type: {type(price)}")

            if price >= CURRENCY_RATES.RATES['BGL']:
                return f"\u001b[0;35m{price/CURRENCY_RATES.RATES['BGL']:.1f} BGL\u001b[0m"
            elif price >= CURRENCY_RATES.RATES['DL']:
                return f"\u001b[0;34m{price/CURRENCY_RATES.RATES['DL']:.0f} DL\u001b[0m"
            return f"\u001b[0;32m{int(price)} WL\u001b[0m"
        except Exception as e:
            self.logger.error(f"Error formatting price {price}: {e}")
            return "Invalid Price"

    async def update_stock_display(self) -> bool:
        """Update tampilan stock tanpa mengirim pesan baru"""
        try:
            channel = self.bot.get_channel(self.stock_channel_id)
            if not channel:
                self.logger.error(f"Channel stock dengan ID {self.stock_channel_id} tidak ditemukan")
                return False

            embed = await self.create_stock_embed()

            if not self.current_stock_message:
                self.current_stock_message = await self.find_last_message()

            if not self.current_stock_message:
                view = self.button_manager.create_view() if self.button_manager else None
                self.current_stock_message = await channel.send(embed=embed, view=view)
                return True

            try:
                await self.current_stock_message.edit(embed=embed)
                return True

            except discord.NotFound:
                self.logger.warning(MESSAGES.WARNING['MESSAGE_NOT_FOUND'])
                self.current_stock_message = None
                view = self.button_manager.create_view() if self.button_manager else None
                self.current_stock_message = await channel.send(embed=embed, view=view)
                return True

        except Exception as e:
            self.logger.error(f"Error updating stock display: {e}")
            return False

    async def cleanup(self):
        """Cleanup resources dengan proper error handling"""
        try:
            if self.current_stock_message:
                embed = discord.Embed(
                    title="🔧 Maintenance",
                    description=MESSAGES.INFO['MAINTENANCE'],
                    color=COLORS.WARNING
                )
                await self.current_stock_message.edit(embed=embed)

            patterns = [
                'live_stock_*',
                'stock_count_*',
                'all_products_display'
            ]
            for pattern in patterns:
                await self.cache_manager.delete_pattern(pattern)

            self.logger.info("LiveStockManager cleanup completed")

        except Exception as e:
            self.logger.error(f"Error in cleanup: {e}")

class LiveStockCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.stock_manager = LiveStockManager(bot)
        self.logger = logging.getLogger("LiveStockCog")
        self._ready = asyncio.Event()
        self.update_stock_task = None
        self.logger.info("LiveStockCog instance created")
        asyncio.create_task(self.stock_manager.initialize())

    async def start_tasks(self):
        """Start background tasks safely"""
        try:
            self.logger.info("Attempting to start stock update task...")
            self.update_stock_task = self.update_stock.start()
            self.logger.info("Stock update task started successfully")
        except Exception as e:
            self.logger.error(f"Failed to start tasks: {e}")
            raise

    async def cog_load(self):
        """Setup when cog is loaded"""
        try:
            self.logger.info("LiveStockCog loading...")
            
            # Setup dasar tanpa menunggu bot ready
            self._ready.set()  # Set ready flag awal
            
            # Schedule delayed setup
            self.bot.loop.create_task(self.delayed_setup())
            self.logger.info("LiveStockCog base loading complete")
    
        except Exception as e:
            self.logger.error(f"Error in cog_load: {e}")
            raise
    
    async def delayed_setup(self):
        """Setup yang membutuhkan bot ready"""
        try:
            self.logger.info("Starting delayed setup...")
            
            # Tunggu bot ready dengan timeout
            try:
                async with asyncio.timeout(30):  # 30 detik timeout
                    self.logger.info("Waiting for bot to be ready...")
                    await self.bot.wait_until_ready()
                    self.logger.info("Bot is ready, proceeding with initialization...")
            except asyncio.TimeoutError:
                self.logger.error("Timeout waiting for bot ready")
                return
    
            # Initialize channel
            channel = self.bot.get_channel(self.stock_manager.stock_channel_id)
            if not channel:
                self.logger.error(f"Stock channel {self.stock_manager.stock_channel_id} not found")
                return
            
            # Start background tasks
            await self.start_tasks()
            self.logger.info("LiveStockCog fully initialized")
    
        except Exception as e:
            self.logger.error(f"Error in delayed setup: {e}")

    async def cog_unload(self):
        """Cleanup when cog is unloaded"""
        try:
            if self.update_stock_task:
                self.update_stock_task.cancel()
            await self.stock_manager.cleanup()
            self.logger.info("LiveStockCog unloaded")
        except Exception as e:
            self.logger.error(f"Error in cog_unload: {e}")

    @tasks.loop(seconds=UPDATE_INTERVAL.LIVE_STOCK)
    async def update_stock(self):
        """Update stock display periodically"""
        if not self._ready.is_set():
            return

        try:
            await self.stock_manager.update_stock_display()
        except Exception as e:
            self.logger.error(f"Error in stock update loop: {e}")

    @update_stock.before_loop
    async def before_update_stock(self):
        """Wait until bot is ready before starting the loop"""
        await self.bot.wait_until_ready()

async def setup(bot):
    """Setup cog dengan proper error handling"""
    try:
        if not hasattr(bot, COG_LOADED['LIVE_STOCK']):
            cog = LiveStockCog(bot)
            await bot.add_cog(cog)
            
            # Tunggu stock manager ready
            try:
                async with asyncio.timeout(5):  # 5 detik timeout
                    await cog.stock_manager._ready.wait()
            except asyncio.TimeoutError:
                logging.error("Timeout waiting for stock manager initialization")
                raise RuntimeError("Stock manager initialization timeout")
            setattr(bot, COG_LOADED['LIVE_STOCK'], True)
            logging.info(f'LiveStock cog loaded at {datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC')
            return True
    except Exception as e:
        logging.error(f"Failed to load LiveStock cog: {e}")
        if hasattr(bot, COG_LOADED['LIVE_STOCK']):
            delattr(bot, COG_LOADED['LIVE_STOCK'])
        raise

async def teardown(bot):
    """Cleanup when extension is unloaded"""
    try:
        if hasattr(bot, COG_LOADED['LIVE_STOCK']):
            cog = bot.get_cog('LiveStockCog')
            if cog:
                await bot.remove_cog('LiveStockCog')
                if hasattr(cog, 'stock_manager'):
                    await cog.stock_manager.cleanup()
            delattr(bot, COG_LOADED['LIVE_STOCK'])
            logging.info("LiveStock extension unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading LiveStock extension: {e}")