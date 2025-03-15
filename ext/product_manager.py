"""
Product Manager Service
Author: fdyytu1
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-15 12:07:52 UTC
"""

import logging
import asyncio
from typing import Dict, List, Optional, Any
from datetime import datetime
import hashlib

import discord
from discord.ext import commands

from .constants import (
    Status,
    TransactionError,
    CACHE_TIMEOUT,
    MESSAGES,
    Stock,
    COLORS,
    NOTIFICATION_CHANNELS
)
from database import get_connection
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager

class ProductManagerResponse:
    """Response handler untuk semua operasi product manager"""
    def __init__(self, success: bool, data: Any = None, message: str = "", error: str = ""):
        self.success = success
        self.data = data 
        self.message = message
        self.error = error
        self.timestamp = datetime.utcnow()
        
        # Fields untuk product
        self.product_code = None
        self.product_name = None
        self.product_price = None
        self.description = None
        
        # Fields untuk stock
        self.stock_count = None
        self.stock_items = None
        self.stock_status = None
        
        # Fields untuk transaksi
        self.buyer_id = None
        self.quantity = None
        self.total_price = None
        self.transaction_type = None
        
        # Fields untuk world info
        self.world = None
        self.owner = None
        self.bot_name = None
        self.world_status = None

    @classmethod
    def success(cls, data: Any = None, message: str = "") -> 'ProductManagerResponse':
        return cls(True, data, message)

    @classmethod
    def error(cls, error: str, message: str = "") -> 'ProductManagerResponse':
        return cls(False, None, message, error)

    def set_product_info(self, code: str, name: str, price: int, description: str = None):
        """Set informasi produk"""
        self.product_code = code
        self.product_name = name
        self.product_price = price
        self.description = description
        return self

    def set_stock_info(self, count: int, items: List = None, status: str = None):
        """Set informasi stok"""
        self.stock_count = count
        self.stock_items = items
        self.stock_status = status
        return self

    def set_transaction_info(self, buyer_id: str, quantity: int, total_price: int, type: str):
        """Set informasi transaksi"""
        self.buyer_id = buyer_id
        self.quantity = quantity
        self.total_price = total_price
        self.transaction_type = type
        return self

    def set_world_info(self, world: str, owner: str, bot_name: str, status: str):
        """Set informasi world"""
        self.world = world
        self.owner = owner
        self.bot_name = bot_name
        self.world_status = status
        return self

    def to_dict(self) -> Dict:
        """Convert response ke dictionary"""
        result = {
            'success': self.success,
            'data': self.data,
            'message': self.message,
            'error': self.error,
            'timestamp': self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }
        
        # Add product info if exists
        if self.product_code:
            result.update({
                'product': {
                    'code': self.product_code,
                    'name': self.product_name,
                    'price': self.product_price,
                    'description': self.description
                }
            })
            
        # Add stock info if exists
        if self.stock_count is not None:
            result.update({
                'stock': {
                    'count': self.stock_count,
                    'items': self.stock_items,
                    'status': self.stock_status
                }
            })
            
        # Add transaction info if exists
        if self.buyer_id:
            result.update({
                'transaction': {
                    'buyer_id': self.buyer_id,
                    'quantity': self.quantity,
                    'total_price': self.total_price,
                    'type': self.transaction_type
                }
            })
            
        # Add world info if exists
        if self.world:
            result.update({
                'world': {
                    'name': self.world,
                    'owner': self.owner,
                    'bot': self.bot_name,
                    'status': self.world_status
                }
            })
            
        return result

class ProductCallbackManager:
    """Manager untuk mengelola callbacks product service"""
    def __init__(self):
        self.callbacks = {
            'product_created': [],
            'product_updated': [],
            'stock_added': [],
            'stock_updated': [],
            'stock_sold': [],
            'world_updated': [],
            'error': []
        }
    
    def register(self, event_type: str, callback: Any):
        """Register callback untuk event tertentu"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)
    
    async def trigger(self, event_type: str, *args: Any, **kwargs: Any):
        """Trigger semua callback untuk event tertentu"""
        if event_type in self.callbacks:
            for callback in self.callbacks[event_type]:
                try:
                    await callback(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in {event_type} callback: {e}")

class ProductManagerService(BaseLockHandler):
    _instance = None
    _instance_lock = asyncio.Lock()

    def __new__(cls, bot):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance.initialized = False
        return cls._instance

    def __init__(self, bot):
        if not self.initialized:
            super().__init__()
            self.bot = bot
            self.logger = logging.getLogger("ProductManagerService")
            self.cache_manager = CacheManager()
            self.callback_manager = ProductCallbackManager()
            self.setup_default_callbacks()
            self.initialized = True

    def setup_default_callbacks(self):
        """Setup default callbacks untuk notifikasi"""
        
        async def notify_product_created(product: Dict):
            """Callback untuk notifikasi produk baru"""
            channel_id = NOTIFICATION_CHANNELS.get('product_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="New Product Created",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="Price", value=f"{product['price']:,} WL")
                if product['description']:
                    embed.add_field(name="Description", value=product['description'])
                await channel.send(embed=embed)
        
        async def notify_stock_added(product_code: str, quantity: int, added_by: str):
            """Callback untuk notifikasi penambahan stock"""
            channel_id = NOTIFICATION_CHANNELS.get('stock_logs')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Stock Added",
                    description=f"Product: {product_code}",
                    color=COLORS.INFO
                )
                embed.add_field(name="Quantity", value=str(quantity))
                embed.add_field(name="Added By", value=added_by)
                await channel.send(embed=embed)
        
        async def notify_stock_sold(product: Dict, buyer: str, quantity: int):
            """Callback untuk notifikasi penjualan"""
            channel_id = NOTIFICATION_CHANNELS.get('transactions')
            if channel := self.bot.get_channel(channel_id):
                embed = discord.Embed(
                    title="Product Sold",
                    description=f"Product: {product['name']} ({product['code']})",
                    color=COLORS.SUCCESS
                )
                embed.add_field(name="Buyer", value=buyer)
                embed.add_field(name="Quantity", value=str(quantity))
                embed.add_field(name="Total Price", value=f"{product['price'] * quantity:,} WL")
                await channel.send(embed=embed)
        
        # Register default callbacks
        self.callback_manager.register('product_created', notify_product_created)
        self.callback_manager.register('stock_added', notify_stock_added)
        self.callback_manager.register('stock_sold', notify_stock_sold)

    async def get_product(self, code: str) -> ProductManagerResponse:
        """Get product dengan info lengkap"""
        cache_key = f"product_{code}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            response = ProductManagerResponse.success(cached)
            response.set_product_info(
                code=cached['code'],
                name=cached['name'],
                price=cached['price'],
                description=cached.get('description')
            )
            return response

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                "SELECT * FROM products WHERE code = ? COLLATE NOCASE",
                (code,)
            )
            
            result = cursor.fetchone()
            if not result:
                return ProductManagerResponse.error(MESSAGES.ERROR['PRODUCT_NOT_FOUND'])

            product = dict(result)
            
            # Get stock count
            stock_count = await self._get_stock_count_internal(code)
            
            response = ProductManagerResponse.success(product)
            response.set_product_info(
                code=product['code'],
                name=product['name'],
                price=product['price'],
                description=product.get('description')
            )
            response.set_stock_info(count=stock_count)
            
            await self.cache_manager.set(
                cache_key,
                product,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            
            return response

        except Exception as e:
            self.logger.error(f"Error getting product: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

    async def get_all_products(self) -> ProductManagerResponse:
        """Get all products dengan stock count"""
        cached = await self.cache_manager.get("all_products")
        if cached:
            return ProductManagerResponse.success(cached)

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM products ORDER BY code")
            
            products = []
            for row in cursor.fetchall():
                product = dict(row)
                stock_count = await self._get_stock_count_internal(product['code'])
                
                response = ProductManagerResponse.success(product)
                response.set_product_info(
                    code=product['code'],
                    name=product['name'],
                    price=product['price'],
                    description=product.get('description')
                )
                response.set_stock_info(count=stock_count)
                products.append(response.to_dict())
            
            await self.cache_manager.set(
                "all_products",
                products,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )
            
            return ProductManagerResponse.success(products)

        except Exception as e:
            self.logger.error(f"Error getting all products: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()

    async def create_product(self, code: str, name: str, price: int, description: str = None) -> ProductManagerResponse:
        """Create product baru"""
        if price < Stock.MIN_PRICE:
            return ProductManagerResponse.error(MESSAGES.ERROR['INVALID_AMOUNT'])

        lock = await self.acquire_lock(f"product_create_{code}")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            # Check if product exists
            existing = await self.get_product(code)
            if existing.success:
                return ProductManagerResponse.error(f"Product with code '{code}' already exists")

            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute(
                """
                INSERT INTO products (code, name, price, description)
                VALUES (?, ?, ?, ?)
                """,
                (code, name, price, description)
            )
            
            conn.commit()
            
            product = {
                'code': code,
                'name': name,
                'price': price,
                'description': description
            }
            
            response = ProductManagerResponse.success(product, "Product created successfully")
            response.set_product_info(code, name, price, description)
            response.set_stock_info(count=0)
            
            # Update cache
            await self.cache_manager.set(
                f"product_{code}",
                product,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.MEDIUM)
            )
            await self.cache_manager.delete("all_products")
            
            # Trigger callback
            await self.callback_manager.trigger('product_created', product)
            
            return response

        except Exception as e:
            self.logger.error(f"Error creating product: {e}")
            if conn:
                conn.rollback()
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"product_create_{code}")

    async def add_stock(self, product_code: str, content: str, added_by: str) -> ProductManagerResponse:
        """Add stock item dengan validasi lengkap"""
        try:
            # Validasi content
            content = content.strip()
            if not content:
                return ProductManagerResponse.error("Content cannot be empty")
            if '\n' in content:
                return ProductManagerResponse.error("Content must be single line")

            # Validasi product
            product_response = await self.get_product(product_code)
            if not product_response.success:
                return product_response

            # Get current stock count
            stock_count = await self._get_stock_count_internal(product_code)
            if stock_count >= Stock.MAX_STOCK:
                return ProductManagerResponse.error(f"Stock limit reached ({Stock.MAX_STOCK})")

            # Create unique lock key
            content_hash = hashlib.md5(content.encode()).hexdigest()
            lock_key = f"stock_add_{product_code}_{content_hash}"
            
            lock = await self.acquire_lock(lock_key)
            if not lock:
                return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

            try:
                conn = get_connection()
                cursor = conn.cursor()
                
                # Check duplicates
                cursor.execute(
                    """
                    SELECT id FROM stock 
                    WHERE product_code = ? AND content = ? 
                    AND status != ?
                    """,
                    (product_code, content, Status.DELETED.value)
                )
                
                if cursor.fetchone():
                    return ProductManagerResponse.error("Duplicate stock content detected")
                
                cursor.execute(
                    """
                    INSERT INTO stock (product_code, content, added_by, status)
                    VALUES (?, ?, ?, ?)
                    """,
                    (product_code, content, added_by, Status.AVAILABLE.value)
                )
                
                conn.commit()
                
                # Update caches
                await self.cache_manager.delete(f"stock_count_{product_code}")
                for i in range(1, Stock.MAX_ITEMS + 1):
                    await self.cache_manager.delete(f"stock_{product_code}_q{i}")
                
                # Get updated stock count
                new_count = await self._get_stock_count_internal(product_code)
                
                response = ProductManagerResponse.success(None, "Stock added successfully")
                response.set_product_info(
                    code=product_code,
                    name=product_response.product_name,
                    price=product_response.product_price,
                    description=product_response.description
                )
                response.set_stock_info(
                    count=new_count,
                    items=[content],
                    status=Status.AVAILABLE.value
                )
                
                # Trigger callback
                await self.callback_manager.trigger(
                    'stock_added', 
                    product_code, 
                    1, 
                    added_by
                )
                
                return response

            except Exception as e:
                if conn:
                    conn.rollback()
                raise e
            finally:
                if conn:
                    conn.close()
                self.release_lock(lock_key)

        except Exception as e:
            self.logger.error(f"Error adding stock: {e}")
            return ProductManagerResponse.error(str(e))

    async def get_available_stock(self, product_code: str, quantity: int = 1) -> ProductManagerResponse:
        """Get available stock dengan proper locking"""
        if quantity < 1:
            return ProductManagerResponse.error(MESSAGES.ERROR['INVALID_AMOUNT'])
            
        cache_key = f"stock_{product_code}_q{quantity}"
        cached = await self.cache_manager.get(cache_key)
        if cached:
            response = ProductManagerResponse.success(cached)
            response.set_stock_info(
                count=len(cached),
                items=cached,
                status=Status.AVAILABLE.value
            )
            return response

        lock = await self.acquire_lock(f"stock_get_{product_code}")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT id, content, added_at
                FROM stock
                WHERE product_code = ? AND status = ?
                ORDER BY added_at ASC
                LIMIT ?
            """, (product_code, Status.AVAILABLE.value, quantity))
            
            stock_items = [{
                'id': row['id'],
                'content': row['content'],
                'added_at': row['added_at']
            } for row in cursor.fetchall()]

            await self.cache_manager.set(
                cache_key, 
                stock_items,
                expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
            )

            response = ProductManagerResponse.success(stock_items)
            response.set_stock_info(
                count=len(stock_items),
                items=stock_items,
                status=Status.AVAILABLE.value
            )
            return response

        except Exception as e:
            self.logger.error(f"Error getting available stock: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_get_{product_code}")

    async def update_stock_status(
        self,
        stock_id: int,
        status: str,
        buyer_id: str = None
    ) -> ProductManagerResponse:
        """Update stock status dengan proper locking"""
        if status not in [s.value for s in Status]:
            return ProductManagerResponse.error(f"Invalid status: {status}")
            
        lock = await self.acquire_lock(f"stock_update_{stock_id}")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            # Get product code first for cache invalidation
            cursor.execute(
                "SELECT product_code, content FROM stock WHERE id = ?", 
                (stock_id,)
            )
            stock_info = cursor.fetchone()
            if not stock_info:
                return ProductManagerResponse.error(MESSAGES.ERROR['STOCK_NOT_FOUND'])
            
            product_code = stock_info['product_code']
            stock_content = stock_info['content']
            
            # Get product info
            product_response = await self.get_product(product_code)
            if not product_response.success:
                return product_response
            
            update_query = """
                UPDATE stock 
                SET status = ?, updated_at = CURRENT_TIMESTAMP
            """
            params = [status]

            if buyer_id:
                update_query += ", buyer_id = ?"
                params.append(buyer_id)

            update_query += " WHERE id = ?"
            params.append(stock_id)

            cursor.execute(update_query, params)
            conn.commit()
            
            # Invalidate relevant caches
            await self.cache_manager.delete(f"stock_count_{product_code}")
            for i in range(1, Stock.MAX_ITEMS + 1):
                await self.cache_manager.delete(f"stock_{product_code}_q{i}")
            
            # Get updated stock count
            new_count = await self._get_stock_count_internal(product_code)
            
            response = ProductManagerResponse.success(None, "Stock status updated successfully")
            response.set_product_info(
                code=product_code,
                name=product_response.product_name,
                price=product_response.product_price
            )
            response.set_stock_info(
                count=new_count,
                items=[{'id': stock_id, 'content': stock_content}],
                status=status
            )
            if buyer_id:
                response.set_transaction_info(
                    buyer_id=buyer_id,
                    quantity=1,
                    total_price=product_response.product_price,
                    type='purchase'
                )
            
            # Trigger callbacks
            await self.callback_manager.trigger('stock_updated', stock_id, status)
            if status == Status.SOLD.value and buyer_id:
                await self.callback_manager.trigger(
                    'stock_sold',
                    product_response.data,
                    buyer_id,
                    1
                )
            
            return response

        except Exception as e:
            self.logger.error(f"Error updating stock status: {e}")
            if conn:
                conn.rollback()
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock(f"stock_update_{stock_id}")

    async def _get_stock_count_internal(self, product_code: str) -> int:
        """Internal method untuk get stock count"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("""
                SELECT COUNT(*) as count 
                FROM stock 
                WHERE product_code = ? AND status = ?
            """, (product_code, Status.AVAILABLE.value))
            
            return cursor.fetchone()['count']
        finally:
            if conn:
                conn.close()

    async def get_world_info(self) -> ProductManagerResponse:
        """Get world info dengan caching"""
        cached = await self.cache_manager.get("world_info")
        if cached:
            response = ProductManagerResponse.success(cached)
            response.set_world_info(
                world=cached['world'],
                owner=cached['owner'],
                bot_name=cached['bot'],
                status=cached['status']
            )
            return response

        lock = await self.acquire_lock("world_info_get")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("SELECT * FROM world_info WHERE id = 1")
            result = cursor.fetchone()
            
            if result:
                info = dict(result)
                await self.cache_manager.set(
                    "world_info", 
                    info,
                    expires_in=CACHE_TIMEOUT.get_seconds(CACHE_TIMEOUT.SHORT)
                )
                
                response = ProductManagerResponse.success(info)
                response.set_world_info(
                    world=info['world'],
                    owner=info['owner'],
                    bot_name=info['bot'],
                    status=info['status']
                )
                return response
                
            return ProductManagerResponse.error(MESSAGES.ERROR['WORLD_INFO_NOT_FOUND'])

        except Exception as e:
            self.logger.error(f"Error getting world info: {e}")
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock("world_info_get")

    async def update_world_info(
        self,
        world: str,
        owner: str,
        bot: str,
        status: str = 'online'
    ) -> ProductManagerResponse:
        """Update world info dengan proper locking"""
        lock = await self.acquire_lock("world_info_update")
        if not lock:
            return ProductManagerResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

        try:
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                UPDATE world_info 
                SET world = ?, owner = ?, bot = ?, status = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = 1
            """, (world, owner, bot, status))
            
            conn.commit()
            
            # Invalidate cache
            await self.cache_manager.delete("world_info")
            
            result = {
                'world': world,
                'owner': owner,
                'bot': bot,
                'status': status
            }
            
            response = ProductManagerResponse.success(result, "World info updated successfully")
            response.set_world_info(world, owner, bot, status)
            
            # Trigger callback
            await self.callback_manager.trigger('world_updated', result)
            
            return response

        except Exception as e:
            self.logger.error(f"Error updating world info: {e}")
            if conn:
                conn.rollback()
            return ProductManagerResponse.error(str(e))
        finally:
            if conn:
                conn.close()
            self.release_lock("world_info_update")

    async def cleanup(self):
        """Cleanup resources before unloading"""
        try:
            patterns = [
                "product_*",
                "stock_*",
                "world_info",
                "all_products"
            ]
            for pattern in patterns:
                await self.cache_manager.delete_pattern(pattern)
            self.logger.info("ProductManagerService cleanup completed")
        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")

    async def verify_dependencies(self) -> bool:
        """Verify all required dependencies are available"""
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return True
        except Exception as e:
            self.logger.error(f"Failed to verify dependencies: {e}")
            return False
        finally:
            if conn:
                conn.close()

class ProductManagerCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.product_service = ProductManagerService(bot)
        self.logger = logging.getLogger("ProductManagerCog")

    async def cog_load(self):
        self.logger.info("ProductManagerCog loading...")
        
    async def cog_unload(self):
        await self.product_service.cleanup()
        self.logger.info("ProductManagerCog unloaded")

async def setup(bot):
    if not hasattr(bot, 'product_manager_loaded'):
        cog = ProductManagerCog(bot)
        
        # Verify dependencies
        if not await cog.product_service.verify_dependencies():
            raise Exception("ProductManager dependencies verification failed")
            
        await bot.add_cog(cog)
        bot.product_manager_loaded = True
        logging.info(
            f'ProductManager cog loaded successfully at '
            f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
        )

async def teardown(bot):
    """Cleanup when extension is unloaded"""
    try:
        if hasattr(bot, 'product_manager_loaded'):
            cog = bot.get_cog('ProductManagerCog')
            if cog:
                await bot.remove_cog('ProductManagerCog')
                await cog.product_service.cleanup()
            delattr(bot, 'product_manager_loaded')
            logging.info("ProductManager extension unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading ProductManager extension: {e}")