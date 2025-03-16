"""
Transaction Manager Service
Author: fdyytu2
Created at: 2025-03-07 18:04:56 UTC
Last Modified: 2025-03-15 21:41:14 UTC

Dependencies:
- database.py: For database connections
- base_handler.py: For lock management
- cache_manager.py: For caching functionality
- product_manager.py: For product operations
- balance_manager.py: For balance operations
"""

import logging
import asyncio
from typing import Optional, Dict, List, Union, Callable, Any
from datetime import datetime
import discord
from discord.ext import commands

from .constants import (
    Status,
    TransactionType,
    Balance,
    TransactionError,
    MESSAGES,
    CACHE_TIMEOUT,
    COLORS,
    EVENTS,
    NOTIFICATION_CHANNELS,
    CURRENCY_RATES
)
from database import get_connection
from .base_handler import BaseLockHandler
from .cache_manager import CacheManager
from .product_manager import ProductManagerService
from .balance_manager import BalanceManagerService

class TransactionQueue:
    """Queue sistem untuk mengelola transaksi"""
    def __init__(self):
        self.queue = asyncio.Queue()
        self._processing = False
        self.logger = logging.getLogger("TransactionQueue")

    async def add_transaction(self, transaction: Dict):
        """Tambah transaksi ke queue"""
        await self.queue.put(transaction)
        if not self._processing:
            await self.process_queue()

    async def process_queue(self):
        """Proses transaksi dalam queue"""
        self._processing = True
        while not self.queue.empty():
            trx = await self.queue.get()
            try:
                await self.process_transaction(trx)
            except Exception as e:
                self.logger.error(f"Error processing transaction: {e}")
            finally:
                self.queue.task_done()
        self._processing = False

class TransactionValidator:
    """Validator untuk transaksi"""
    @staticmethod
    async def validate_purchase(user_id: str, product_code: str, quantity: int):
        """Validate purchase transaction"""
        if quantity <= 0:
            raise ValueError(MESSAGES.ERROR['INVALID_AMOUNT'])
        if not product_code:
            raise ValueError(MESSAGES.ERROR['INVALID_PRODUCT_CODE'])

    @staticmethod
    async def validate_deposit(user_id: str, amount: Dict[str, int]):
        """Validate deposit transaction"""
        total = sum(amount.values())
        if total <= 0:
            raise ValueError(MESSAGES.ERROR['INVALID_AMOUNT'])

class TransactionResponse:
    """Response handler untuk transaksi"""
    def __init__(
        self,
        success: bool,
        transaction_type: str = "",
        data: Any = None,
        message: str = "",
        error: str = "",
        product_response: Any = None,
        balance_response: Any = None
    ):
        self.success = success
        self.transaction_type = transaction_type
        self.data = data
        self.message = message
        self.error = error
        self.product_data = product_response
        self.balance_data = balance_response
        self.timestamp = datetime.utcnow()

    @classmethod
    def success(cls, transaction_type: str, data: Any = None, message: str = "", 
                product_response: Any = None, balance_response: Any = None) -> 'TransactionResponse':
        return cls(True, transaction_type, data, message, 
                  product_response=product_response,
                  balance_response=balance_response)

    @classmethod
    def error(cls, error: str, message: str = "") -> 'TransactionResponse':
        return cls(False, "", None, message, error)

    def to_dict(self) -> Dict:
        """Convert response ke dictionary"""
        return {
            'success': self.success,
            'transaction_type': self.transaction_type,
            'data': self.data,
            'message': self.message,
            'error': self.error,
            'product_data': self.product_data,
            'balance_data': self.balance_data,
            'timestamp': self.timestamp.strftime("%Y-%m-%d %H:%M:%S")
        }

class TransactionCallbackManager:
    """Manager untuk callbacks"""
    def __init__(self):
        self.callbacks = {
            'transaction_started': [],
            'transaction_completed': [],
            'transaction_failed': [],
            'purchase_completed': [],
            'deposit_completed': [],
            'withdrawal_completed': [],
            'error': []
        }

    def register(self, event_type: str, callback: Callable):
        """Register callback untuk event"""
        if event_type in self.callbacks:
            self.callbacks[event_type].append(callback)

    async def trigger(self, event_type: str, *args: Any, **kwargs: Any):
        """Trigger callbacks untuk event"""
        if event_type in self.callbacks:
            for callback in self.callbacks[event_type]:
                try:
                    await callback(*args, **kwargs)
                except Exception as e:
                    logging.error(f"Error in {event_type} callback: {e}")

class TransactionManager(BaseLockHandler):
    def __init__(self, bot):
        if not hasattr(self, 'initialized'):
            super().__init__()
            self.bot = bot
            self.logger = logging.getLogger("TransactionManager")
            self.cache_manager = CacheManager()
            self.product_manager = ProductManagerService(bot)
            self.balance_manager = BalanceManagerService(bot)
            self.callback_manager = TransactionCallbackManager()
            self.transaction_queue = TransactionQueue()
            self.validator = TransactionValidator()
            self.setup_default_callbacks()
            self.initialized = True

    def setup_default_callbacks(self):
        """Setup default callbacks"""
        async def notify_transaction_completed(**data):
            channel_id = NOTIFICATION_CHANNELS.get('transactions')
            if channel := self.bot.get_channel(channel_id):
                embed = self._create_transaction_embed(data)
                await channel.send(embed=embed)

        async def notify_large_transaction(**data):
            if 'total_wl' in data and data['total_wl'] > 100000:
                channel_id = NOTIFICATION_CHANNELS.get('admin_logs')
                if channel := self.bot.get_channel(channel_id):
                    embed = discord.Embed(
                        title="⚠️ Large Transaction Alert",
                        description="Transaction above 100K WL detected",
                        color=COLORS.WARNING
                    )
                    await channel.send(embed=embed)

        self.callback_manager.register('transaction_completed', notify_transaction_completed)
        self.callback_manager.register('transaction_completed', notify_large_transaction)

    async def process_purchase(self, buyer_id: str, product_code: str, quantity: int = 1) -> TransactionResponse:
        """Process purchase transaction"""
        try:
            # Validate input
            await self.validator.validate_purchase(buyer_id, product_code, quantity)

            lock = await self.acquire_lock(f"purchase_{buyer_id}_{product_code}")
            if not lock:
                return TransactionResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

            # Get GrowID
            growid_response = await self.balance_manager.get_growid(buyer_id)
            if not growid_response.success:
                return TransactionResponse.error(growid_response.error)
            growid = growid_response.data

            # Get product
            product_response = await self.product_manager.get_product(product_code)
            if not product_response.success:
                return TransactionResponse.error(product_response.error)
            product = product_response.data

            # Get stock
            stock_response = await self.product_manager.get_available_stock(product_code, quantity)
            if not stock_response.success:
                return TransactionResponse.error(stock_response.error)
            available_stock = stock_response.data

            # Calculate price
            total_price = product['price'] * quantity

            # Verify balance
            balance_response = await self.balance_manager.get_balance(growid)
            if not balance_response.success:
                return TransactionResponse.error(balance_response.error)
            current_balance = balance_response.data

            if total_price > current_balance.total_wl():
                return TransactionResponse.error(MESSAGES.ERROR['INSUFFICIENT_BALANCE'])

            # Update stock
            stock_update = await self.product_manager.update_stock_status(
                product_code,
                [item['id'] for item in available_stock[:quantity]],
                Status.SOLD.value,
                buyer_id
            )
            if not stock_update.success:
                return TransactionResponse.error(stock_update.error)

            # Update balance
            balance_update = await self.balance_manager.update_balance(
                growid=growid,
                wl=-total_price,
                details=f"Purchase {quantity}x {product['name']}",
                transaction_type=TransactionType.PURCHASE.value
            )
            if not balance_update.success:
                # Rollback stock
                await self.product_manager.update_stock_status(
                    product_code,
                    [item['id'] for item in available_stock[:quantity]],
                    Status.AVAILABLE.value,
                    None
                )
                return TransactionResponse.error(balance_update.error)

            # Send notification
            await self._send_transaction_notification(
                buyer_id,
                TransactionType.PURCHASE.value,
                {
                    'product': product['name'],
                    'quantity': quantity,
                    'total_price': total_price,
                    'new_balance': balance_update.data
                }
            )

            return TransactionResponse.success(
                transaction_type=TransactionType.PURCHASE.value,
                data={
                    'product': product,
                    'quantity': quantity,
                    'total_price': total_price,
                    'content': [item['content'] for item in available_stock[:quantity]]
                },
                message=f"Successfully purchased {quantity}x {product['name']}",
                product_response=product_response,
                balance_response=balance_update
            )

        except Exception as e:
            self.logger.error(f"Error processing purchase: {e}")
            return TransactionResponse.error(str(e))
        finally:
            self.release_lock(f"purchase_{buyer_id}_{product_code}")

    async def process_deposit(
        self, 
        user_id: str, 
        wl: int = 0, 
        dl: int = 0, 
        bgl: int = 0
    ) -> TransactionResponse:
        """Process deposit transaction"""
        try:
            await self.validator.validate_deposit(user_id, {'wl': wl, 'dl': dl, 'bgl': bgl})

            lock = await self.acquire_lock(f"deposit_{user_id}")
            if not lock:
                return TransactionResponse.error(MESSAGES.ERROR['LOCK_ACQUISITION_FAILED'])

            # Get GrowID
            growid_response = await self.balance_manager.get_growid(user_id)
            if not growid_response.success:
                return TransactionResponse.error(growid_response.error)
            growid = growid_response.data

            # Calculate total
            total_wl = wl + (dl * CURRENCY_RATES.RATES['DL']) + (bgl * CURRENCY_RATES.RATES['BGL'])

            # Update balance
            balance_response = await self.balance_manager.update_balance(
                growid=growid,
                wl=wl,
                dl=dl,
                bgl=bgl,
                details=f"Deposit: {wl}WL, {dl}DL, {bgl}BGL",
                transaction_type=TransactionType.DEPOSIT.value
            )

            if not balance_response.success:
                return TransactionResponse.error(balance_response.error)

            # Send notification
            await self._send_transaction_notification(
                user_id,
                TransactionType.DEPOSIT.value,
                {
                    'amount': f"{wl}WL, {dl}DL, {bgl}BGL",
                    'total_wl': total_wl,
                    'new_balance': balance_response.data
                }
            )

            return TransactionResponse.success(
                transaction_type=TransactionType.DEPOSIT.value,
                data={'total_deposited': total_wl},
                message=f"Successfully deposited {total_wl:,} WL",
                balance_response=balance_response
            )

        except Exception as e:
            self.logger.error(f"Error processing deposit: {e}")
            return TransactionResponse.error(str(e))
        finally:
            self.release_lock(f"deposit_{user_id}")

    async def get_transaction_history(
        self,
        user_id: str,
        limit: int = 10,
        offset: int = 0,
        transaction_type: str = None,
        start_date: datetime = None,
        end_date: datetime = None
    ) -> TransactionResponse:
        """Get transaction history"""
        try:
            # Get GrowID
            growid_response = await self.balance_manager.get_growid(user_id)
            if not growid_response.success:
                return TransactionResponse.error(growid_response.error)
            growid = growid_response.data

            # Get history from balance manager
            history_response = await self.balance_manager.get_transaction_history(
                growid,
                limit=limit,
                offset=offset,
                transaction_type=transaction_type,
                start_date=start_date,
                end_date=end_date
            )

            if not history_response.success:
                return TransactionResponse.error(history_response.error)

            transactions = history_response.data
            if not transactions:
                return TransactionResponse.error(MESSAGES.ERROR['NO_HISTORY'])

            # Format transactions
            formatted_transactions = []
            for trx in transactions:
                formatted_trx = self._format_transaction(trx)
                if formatted_trx:
                    formatted_transactions.append(formatted_trx)

            return TransactionResponse.success(
                transaction_type='history',
                data=formatted_transactions,
                message=f"Found {len(formatted_transactions)} transactions"
            )

        except Exception as e:
            self.logger.error(f"Error getting transaction history: {e}")
            return TransactionResponse.error(str(e))

    async def _send_transaction_notification(
        self,
        user_id: str,
        transaction_type: str,
        details: Dict
    ) -> None:
        """Send transaction notification"""
        try:
            user = await self.bot.fetch_user(int(user_id))
            if not user:
                return

            embed = self._create_transaction_embed(
                {
                    'type': transaction_type,
                    'user_id': user_id,
                    **details
                }
            )
            
            await user.send(embed=embed)
        except Exception as e:
            self.logger.error(f"Error sending notification: {e}")

    def _create_transaction_embed(self, data: Dict) -> discord.Embed:
        """Create transaction embed"""
        transaction_type = data.get('type', 'Unknown')
        embed = discord.Embed(
            title=f"{'✅' if data.get('success', True) else '❌'} {transaction_type.title()} Transaction",
            color=COLORS.SUCCESS if data.get('success', True) else COLORS.ERROR,
            timestamp=datetime.utcnow()
        )

        for key, value in data.items():
            if key not in ['type', 'success']:
                embed.add_field(
                    name=key.replace('_', ' ').title(),
                    value=str(value),
                    inline=False
                )
        return embed

    def _format_transaction(self, transaction: Dict) -> Optional[Dict]:
        """Format transaction data"""
        try:
            # Format timestamp
            created_at = datetime.fromisoformat(transaction['created_at'].replace('Z', '+00:00'))
            formatted_date = created_at.strftime('%Y-%m-%d %H:%M:%S UTC')

            # Calculate balance change
            old_balance = Balance.from_string(transaction['old_balance'])
            new_balance = Balance.from_string(transaction['new_balance'])
            balance_change = new_balance.total_wl() - old_balance.total_wl()

            return {
                'id': transaction['id'],
                'type': transaction['type'],
                'date': formatted_date,
                'amount': self._format_amount(abs(balance_change)),
                'change': f"{'+' if balance_change >= 0 else '-'}{self._format_amount(abs(balance_change))}",
                'details': transaction['details'],
                'status': transaction['status'],
                'old_balance': old_balance.format(),
                'new_balance': new_balance.format()
            }
        except Exception as e:
            self.logger.error(f"Error formatting transaction: {e}")
            return None

    def _format_amount(self, amount: int) -> str:
        """Format amount dengan currency rates"""
        try:
            if amount >= CURRENCY_RATES.RATES['BGL']:
                return f"{amount/CURRENCY_RATES.RATES['BGL']:.1f} BGL"
            elif amount >= CURRENCY_RATES.RATES['DL']:
                return f"{amount/CURRENCY_RATES.RATES['DL']:.0f} DL"
            return f"{amount:,} WL"
        except Exception as e:
            self.logger.error(f"Error formatting amount: {e}")
            return f"{amount:,} WL"

    async def process_batch_transaction(
        self,
        transactions: List[Dict[str, Any]]
    ) -> List[TransactionResponse]:
        """
        Process multiple transactions in batch
        
        Args:
            transactions: List of transaction details
                [
                    {
                        'type': 'purchase/deposit/withdrawal',
                        'user_id': str,
                        'product_code': str,
                        'quantity': int,
                        'amount': Dict[str, int]
                    }
                ]
        """
        results = []
        for trx in transactions:
            try:
                if trx['type'] == TransactionType.PURCHASE.value:
                    result = await self.process_purchase(
                        trx['user_id'],
                        trx['product_code'],
                        trx['quantity']
                    )
                elif trx['type'] == TransactionType.DEPOSIT.value:
                    result = await self.process_deposit(
                        trx['user_id'],
                        **trx['amount']
                    )
                else:
                    result = TransactionResponse.error(
                        f"Unsupported transaction type: {trx['type']}"
                    )
                results.append(result)
            except Exception as e:
                self.logger.error(f"Error processing batch transaction: {e}")
                results.append(TransactionResponse.error(str(e)))
        return results

    async def get_transaction_analytics(
        self,
        timeframe: str = 'daily'
    ) -> Dict:
        """
        Get transaction analytics
        
        Args:
            timeframe: 'daily', 'weekly', 'monthly'
        """
        try:
            analytics = {
                'total_transactions': 0,
                'total_volume': 0,
                'product_stats': {},
                'user_stats': {},
                'hourly_distribution': {},
                'type_distribution': {},
                'success_rate': 0
            }

            # Get transactions for timeframe
            transactions = await self._get_transactions_for_timeframe(timeframe)
            
            if not transactions:
                return analytics

            success_count = 0
            for trx in transactions:
                try:
                    analytics['total_transactions'] += 1
                    
                    # Calculate volume
                    if 'amount' in trx:
                        analytics['total_volume'] += int(trx['amount'])
                    
                    # Product stats
                    if 'product_code' in trx:
                        if trx['product_code'] not in analytics['product_stats']:
                            analytics['product_stats'][trx['product_code']] = 0
                        analytics['product_stats'][trx['product_code']] += 1
                    
                    # User stats
                    if 'user_id' in trx:
                        if trx['user_id'] not in analytics['user_stats']:
                            analytics['user_stats'][trx['user_id']] = 0
                        analytics['user_stats'][trx['user_id']] += 1
                    
                    # Hourly distribution
                    hour = datetime.fromisoformat(trx['created_at']).hour
                    if hour not in analytics['hourly_distribution']:
                        analytics['hourly_distribution'][hour] = 0
                    analytics['hourly_distribution'][hour] += 1
                    
                    # Type distribution
                    if trx['type'] not in analytics['type_distribution']:
                        analytics['type_distribution'][trx['type']] = 0
                    analytics['type_distribution'][trx['type']] += 1
                    
                    # Success rate
                    if trx.get('status') == 'success':
                        success_count += 1
                        
                except Exception as e:
                    self.logger.error(f"Error processing transaction for analytics: {e}")
                    continue

            # Calculate success rate
            analytics['success_rate'] = (success_count / analytics['total_transactions']) * 100 if analytics['total_transactions'] > 0 else 0
            
            return analytics

        except Exception as e:
            self.logger.error(f"Error getting transaction analytics: {e}")
            return {}

    async def _get_transactions_for_timeframe(
        self,
        timeframe: str
    ) -> List[Dict]:
        """Get transactions for specific timeframe"""
        try:
            now = datetime.utcnow()
            
            if timeframe == 'daily':
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0)
            elif timeframe == 'weekly':
                start_date = now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=now.weekday())
            elif timeframe == 'monthly':
                start_date = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            else:
                raise ValueError(f"Invalid timeframe: {timeframe}")

            # Get transactions from database
            conn = get_connection()
            cursor = conn.cursor()
            
            cursor.execute("""
                SELECT * FROM transactions 
                WHERE created_at >= ? 
                ORDER BY created_at DESC
            """, (start_date.isoformat(),))
            
            transactions = cursor.fetchall()
            
            return transactions

        except Exception as e:
            self.logger.error(f"Error getting transactions for timeframe: {e}")
            return []
        finally:
            if conn:
                conn.close()

    async def recover_failed_transaction(
        self,
        transaction_id: str
    ) -> TransactionResponse:
        """
        Attempt to recover failed transaction
        
        Args:
            transaction_id: ID of failed transaction
        """
        try:
            # Get transaction details
            transaction = await self._get_transaction(transaction_id)
            if not transaction:
                return TransactionResponse.error("Transaction not found")

            if transaction['status'] != 'failed':
                return TransactionResponse.error("Transaction is not in failed state")

            # Attempt recovery based on transaction type
            if transaction['type'] == TransactionType.PURCHASE.value:
                return await self.recover_purchase(transaction)
            elif transaction['type'] == TransactionType.DEPOSIT.value:
                return await self.recover_deposit(transaction)
            else:
                return TransactionResponse.error(f"Recovery not supported for type: {transaction['type']}")

        except Exception as e:
            self.logger.error(f"Error recovering transaction: {e}")
            return TransactionResponse.error(str(e))

    async def monitor_pending_transactions(self):
        """Monitor and recover pending transactions"""
        while True:
            try:
                # Get pending transactions
                conn = get_connection()
                cursor = conn.cursor()
                
                cursor.execute("""
                    SELECT * FROM transactions 
                    WHERE status = 'pending' 
                    AND created_at <= datetime('now', '-5 minutes')
                """)
                
                pending = cursor.fetchall()
                
                for trx in pending:
                    try:
                        await self.recover_failed_transaction(trx['id'])
                    except Exception as e:
                        self.logger.error(f"Error recovering transaction {trx['id']}: {e}")
                
                await asyncio.sleep(300)  # Check every 5 minutes
                
            except Exception as e:
                self.logger.error(f"Error monitoring transactions: {e}")
                await asyncio.sleep(300)
            finally:
                if conn:
                    conn.close()

class TransactionCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.trx_manager = TransactionManager(bot)
        self.logger = logging.getLogger("TransactionCog")

    async def cog_load(self):
        """Setup when cog is loaded"""
        self.logger.info("TransactionCog loading...")
        
        # Start monitoring task
        self.bot.loop.create_task(self.trx_manager.monitor_pending_transactions())

    async def cog_unload(self):
        """Cleanup when cog is unloaded"""
        self.logger.info("TransactionCog unloaded")

async def setup(bot):
    """Setup cog dengan proper error handling"""
    try:
        if not hasattr(bot, 'transaction_manager_loaded'):
            # Verify dependencies
            product_manager = ProductManagerService(bot)
            balance_manager = BalanceManagerService(bot)
            
            if not hasattr(bot, 'product_manager_loaded'):
                raise Exception("ProductManager must be loaded before TransactionManager")
            if not hasattr(bot, 'balance_manager_loaded'):
                raise Exception("BalanceManager must be loaded before TransactionManager")
                
            # Add cog
            cog = TransactionCog(bot)
            await bot.add_cog(cog)
            bot.transaction_manager_loaded = True
            
            logging.info(
                f'Transaction Manager loaded successfully at '
                f'{datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")} UTC'
            )
    except Exception as e:
        logging.error(f"Failed to load TransactionManager: {e}")
        if hasattr(bot, 'transaction_manager_loaded'):
            delattr(bot, 'transaction_manager_loaded')
        raise

async def teardown(bot):
    """Cleanup when extension is unloaded"""
    try:
        if hasattr(bot, 'transaction_manager_loaded'):
            cog = bot.get_cog('TransactionCog')
            if cog:
                await bot.remove_cog('TransactionCog')
            delattr(bot, 'transaction_manager_loaded')
            logging.info("Transaction Manager unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading TransactionManager: {e}")