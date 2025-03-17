"""
Live Buttons Manager with Shop Integration
Version: 2.1.0
Author: fdyytu2
Created at: 2025-03-16 17:27:53 UTC
Last Modified: 2025-03-16 17:27:53 UTC

Dependencies:
- ext.product_manager: For product operations
- ext.balance_manager: For balance operations
- ext.trx: For transaction operations
- ext.admin_service: For maintenance mode
- ext.base_handler: For lock and response handling
- ext.constants: For configuration and responses
"""

import discord
from discord.ext import commands, tasks
from discord import ui
import logging
import asyncio
from datetime import datetime
from typing import List, Dict, Optional, Union
from discord.ui import Select, Button, View, Modal, TextInput

from .constants import (
    COLORS,
    MESSAGES,
    BUTTON_IDS,
    CACHE_TIMEOUT,
    Stock,
    Status,
    CURRENCY_RATES,
    UPDATE_INTERVAL,
    COG_LOADED,
    TransactionType,
    Balance
)

from .base_handler import BaseLockHandler, BaseResponseHandler
from .cache_manager import CacheManager
from .product_manager import ProductManagerService
from .balance_manager import BalanceManagerService
from .trx import TransactionManager
from .admin_service import AdminService

logger = logging.getLogger(__name__)
class PurchaseModal(discord.ui.Modal, BaseResponseHandler):
    def __init__(self, products: List[Dict], balance_service, product_service, trx_manager):
        super().__init__(title="🛍️ Pembelian Produk")
        self.products_cache = {p['code']: p for p in products}
        self.balance_service = balance_service
        self.product_service = product_service
        self.trx_manager = trx_manager
        BaseResponseHandler.__init__(self)

        # Format product list untuk ditampilkan
        product_list = "\n".join([
            f"{p['name']} ({p['code']}) - {p['price']} WL | Stok: {p['stock']}"
            for p in products
        ])

        self.product_info = discord.ui.TextInput(
            label="Daftar Produk (Kode dalam kurung)",
            style=discord.TextStyle.paragraph,
            default=product_list,
            required=False,
            custom_id="product_info"
        )

        self.product_code = discord.ui.TextInput(
            label="Kode Produk",
            style=discord.TextStyle.short,
            placeholder="Masukkan kode produk yang ingin dibeli",
            required=True,
            min_length=1,
            max_length=10,
            custom_id="product_code"
        )

        self.quantity = discord.ui.TextInput(
            label="Jumlah",
            style=discord.TextStyle.short,
            placeholder="Masukkan jumlah yang ingin dibeli",
            required=True,
            min_length=1,
            max_length=3,
            custom_id="quantity"
        )

        self.add_item(self.product_info)
        self.add_item(self.product_code)
        self.add_item(self.quantity)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            response_sent = True
        except discord.errors.InteractionResponded:
            response_sent = True
        except Exception:
            response_sent = False

        try:
            # Get dan validasi input
            product_code = self.product_code.value.strip().upper()
            quantity = int(self.quantity.value)
    
            # Validasi produk
            if product_code not in self.products_cache:
                raise ValueError(f"Produk dengan kode '{product_code}' tidak ditemukan")
            
            selected_product = self.products_cache[product_code]
    
            # Validasi stok
            stock_response = await self.product_service.get_stock_count(product_code)
            if not stock_response.success:
                raise ValueError(stock_response.error)
    
            current_stock = stock_response.data
            if current_stock <= 0:
                raise ValueError(MESSAGES.ERROR['OUT_OF_STOCK'])
            
            if quantity <= 0 or quantity > current_stock:
                raise ValueError(MESSAGES.ERROR['INVALID_AMOUNT'])
    
            # Proses pembelian
            purchase_response = await self.trx_manager.process_purchase(
                buyer_id=str(interaction.user.id),
                product_code=product_code,
                quantity=quantity
            )
    
            # Cek response berhasil atau tidak
            if not purchase_response.success:
                raise ValueError(purchase_response.error)
    
            # Format pesan sukses
            embed = discord.Embed(
                title="✅ Pembelian Berhasil",
                description=purchase_response.message,
                color=COLORS.SUCCESS
            )
    
            # Tambah detail produk jika ada
            if purchase_response.data and 'content' in purchase_response.data:
                content_text = "\n".join(purchase_response.data['content'])
                embed.add_field(
                    name="Detail Produk",
                    value=f"```\n{content_text}\n```",
                    inline=False
                )
    
            # Tambah info tambahan
            if purchase_response.balance_response and purchase_response.balance_response.data:
                embed.add_field(
                    name="Saldo Baru",
                    value=f"```\n{purchase_response.balance_response.data.format()}\n```",
                    inline=False
                )
    
            if response_sent:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
    
        except ValueError as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=COLORS.ERROR
            )
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=MESSAGES.ERROR['TRANSACTION_FAILED'],
                color=COLORS.ERROR
            )
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
class RegisterModal(Modal, BaseResponseHandler):
    def __init__(self, existing_growid=None):
        title = "📝 Update GrowID" if existing_growid else "📝 Pendaftaran GrowID"
        super().__init__(title=title)
        BaseResponseHandler.__init__(self)
        
        self.growid = TextInput(
            label="Masukkan GrowID Anda",
            placeholder=f"GrowID saat ini: {existing_growid}" if existing_growid else "Contoh: GROW_ID",
            min_length=3,
            max_length=30,
            required=True
        )
        self.add_item(self.growid)
        self.existing_growid = existing_growid

    async def on_submit(self, interaction: discord.Interaction):
        try:
            await interaction.response.defer(ephemeral=True)
            response_sent = True
        except discord.errors.InteractionResponded:
            response_sent = True
        except Exception:
            response_sent = False

        try:
            balance_service = BalanceManagerService(interaction.client)

            growid = str(self.growid.value).strip()
            if not growid or len(growid) < 3:
                raise ValueError(MESSAGES.ERROR['INVALID_GROWID'])

            register_response = await balance_service.register_user(
                str(interaction.user.id),
                growid
            )

            if not register_response.success:
                raise ValueError(register_response.error)

            # Buat pesan yang sesuai berdasarkan operasi
            title = "✅ GrowID Diperbarui" if self.existing_growid else "✅ Pendaftaran Berhasil"
            if self.existing_growid:
                description = f"GrowID berhasil diperbarui!\nGrowID Lama: {self.existing_growid}\nGrowID Baru: {growid}"
            else:
                description = MESSAGES.SUCCESS['REGISTRATION'].format(growid=growid)

            success_embed = discord.Embed(
                title=title,
                description=description,
                color=COLORS.SUCCESS
            )

            if response_sent:
                await interaction.followup.send(embed=success_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=success_embed, ephemeral=True)

        except ValueError as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=str(e),
                color=COLORS.ERROR
            )
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
        except Exception as e:
            error_embed = discord.Embed(
                title="❌ Error",
                description=MESSAGES.ERROR['REGISTRATION_FAILED'],
                color=COLORS.ERROR
            )
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
                
class ShopView(View, BaseLockHandler, BaseResponseHandler):
    def __init__(self, bot):
        View.__init__(self, timeout=None)
        BaseLockHandler.__init__(self)
        BaseResponseHandler.__init__(self)
        
        self.bot = bot
        self.balance_service = BalanceManagerService(bot)
        self.product_service = ProductManagerService(bot)
        self.trx_manager = TransactionManager(bot)
        self.admin_service = AdminService(bot)
        self.cache_manager = CacheManager()
        self.logger = logging.getLogger("ShopView")

    async def _handle_interaction_error(self, interaction: discord.Interaction, error_msg: str, ephemeral: bool = True):
        """Helper untuk menangani interaction error"""
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=error_msg,
                        color=COLORS.ERROR
                    ),
                    ephemeral=ephemeral
                )
            else:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description=error_msg,
                        color=COLORS.ERROR
                    ),
                    ephemeral=ephemeral
                )
        except Exception as e:
            self.logger.error(f"Error sending error message: {e}")

    @discord.ui.button(
        style=discord.ButtonStyle.primary,
        label="📝 Daftar/Update",
        custom_id=BUTTON_IDS.REGISTER
    )
    async def register_callback(self, interaction: discord.Interaction, button: Button):
        if not await self.acquire_response_lock(interaction):
            await self._handle_interaction_error(interaction, MESSAGES.INFO['COOLDOWN'])
            return

        try:
            response_sent = False
            
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])

            # Cek GrowID yang sudah ada
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            existing_growid = None
            if growid_response.success and growid_response.data:
                existing_growid = growid_response.data

            try:
                modal = RegisterModal(existing_growid=existing_growid)
                await interaction.response.send_modal(modal)
                response_sent = True
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description="Mohon coba lagi dalam beberapa saat",
                        color=COLORS.ERROR
                    ),
                    ephemeral=True
                )

        except ValueError as e:
            if not response_sent:
                await self._handle_interaction_error(interaction, str(e))
        except Exception as e:
            self.logger.error(f"Error in register callback: {e}")
            if not response_sent:
                await self._handle_interaction_error(
                    interaction, 
                    MESSAGES.ERROR['REGISTRATION_FAILED']
                )
        finally:
            self.release_response_lock(interaction)

    @discord.ui.button(
        style=discord.ButtonStyle.success,
        label="💰 Saldo",
        custom_id=BUTTON_IDS.BALANCE
    )
    async def balance_callback(self, interaction: discord.Interaction, button: Button):
        if not await self.acquire_response_lock(interaction):
            try:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            return
    
        try:
            response_sent = False
            
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])
    
            # Defer response
            try:
                await interaction.response.defer(ephemeral=True)
                response_sent = True
            except discord.errors.InteractionResponded:
                response_sent = True
            except Exception:
                pass
    
            # Get user's GrowID
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            if not growid_response.success:
                raise ValueError(growid_response.error)
    
            growid = growid_response.data
            if not growid:
                raise ValueError(MESSAGES.ERROR['NOT_REGISTERED'])
    
            # Get balance
            balance_response = await self.balance_service.get_balance(growid)
            if not balance_response.success:
                raise ValueError(balance_response.error)
    
            balance = balance_response.data
    
            # Create embed
            embed = discord.Embed(
                title="💰 Informasi Saldo",
                description=f"Saldo untuk `{growid}`",
                color=COLORS.INFO
            )
    
            # Add balance info
            embed.add_field(
                name="Saldo Saat Ini",
                value=f"```yml\n{balance.format()}```",
                inline=False
            )
    
            # Get transaction history
            trx_response = await self.trx_manager.get_transaction_history(growid, limit=3)
            if trx_response.success and trx_response.data:
                transactions = trx_response.data
                trx_details = []
                for trx in transactions:
                    try:
                        trx_details.append(
                            f"• {trx['type']}: {trx['change']} - {trx['details']}"
                        )
                    except Exception:
                        continue
    
                if trx_details:
                    embed.add_field(
                        name="Transaksi Terakhir",
                        value=f"```yml\n{chr(10).join(trx_details)}```",
                        inline=False
                    )
    
            embed.set_footer(text="Diperbarui")
            embed.timestamp = datetime.utcnow()
    
            # Send response
            if response_sent:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
    
        except ValueError as e:
            await self._send_error_response(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in balance callback: {e}")
            await self._send_error_response(
                interaction,
                MESSAGES.ERROR['BALANCE_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)
    
    async def _send_error_response(self, interaction: discord.Interaction, error_message: str, response_sent: bool):
        """Helper method untuk mengirim error response"""
        error_embed = discord.Embed(
            title="❌ Error",
            description=error_message,
            color=COLORS.ERROR
        )
        
        try:
            if response_sent:
                await interaction.followup.send(embed=error_embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
        except Exception as e:
            self.logger.error(f"Error sending error response: {e}")
    @discord.ui.button(
        style=discord.ButtonStyle.secondary,
        label="🌎 World Info",
        custom_id=BUTTON_IDS.WORLD_INFO
    )
    async def world_info_callback(self, interaction: discord.Interaction, button: Button):
        if not await self.acquire_response_lock(interaction):
            try:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            return
    
        try:
            response_sent = False
            
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])
    
            # Defer response
            try:
                await interaction.response.defer(ephemeral=True)
                response_sent = True
            except discord.errors.InteractionResponded:
                response_sent = True
            except Exception:
                pass
    
            # Get world info
            world_response = await self.product_service.get_world_info()
            if not world_response.success:
                raise ValueError(world_response.error)
    
            world_info = world_response.data
            
            # Format status dengan emoji
            status_emoji = {
                'online': '🟢',
                'offline': '🔴',
                'maintenance': '🔧',
                'busy': '🟡',
                'full': '🔵'
            }
            
            status = world_info.get('status', '').lower()
            status_display = f"{status_emoji.get(status, '❓')} {status.upper()}"
    
            # Create embed
            embed = discord.Embed(
                title="🌎 World Information",
                color=COLORS.INFO
            )
    
            # Basic info dalam format yang rapi
            basic_info = [
                f"{'World':<12}: {world_info.get('world', 'N/A')}",
                f"{'Owner':<12}: {world_info.get('owner', 'N/A')}",
                f"{'Bot':<12}: {world_info.get('bot', 'N/A')}",
                f"{'Status':<12}: {status_display}"
            ]
            
            embed.add_field(
                name="Basic Info",
                value="```" + "\n".join(basic_info) + "```",
                inline=False
            )
    
            # Last updated
            updated_at = world_info.get('updated_at')
            if updated_at:
                try:
                    dt = datetime.fromisoformat(updated_at.replace('Z', '+00:00'))
                    last_update = dt.strftime("%Y-%m-%d %H:%M:%S UTC")
                    embed.set_footer(text=f"Last Updated: {last_update}")
                except:
                    pass
    
            # Send response
            if response_sent:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
    
        except ValueError as e:
            await self._send_error_response(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in world info callback: {e}")
            await self._send_error_response(
                interaction,
                MESSAGES.ERROR['WORLD_INFO_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)
    @discord.ui.button(
        style=discord.ButtonStyle.success,
        label="🛒 Beli",
        custom_id=BUTTON_IDS.BUY
    )
    async def buy_callback(self, interaction: discord.Interaction, button: Button):
        if not await self.acquire_response_lock(interaction):
            try:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            return
    
        try:
            response_sent = False
            
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])
    
            # Verify user registration
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            if not growid_response.success:
                raise ValueError(growid_response.error)
    
            # Get available products
            product_response = await self.product_service.get_all_products()
            if not product_response.success or not product_response.data:
                raise ValueError(MESSAGES.ERROR['NO_PRODUCTS'])
    
            # Filter available products
            available_products = []
            for product in product_response.data:
                stock_response = await self.product_service.get_stock_count(product['code'])
                if stock_response.success and stock_response.data > 0:
                    product['stock'] = stock_response.data
                    available_products.append(product)
    
            if not available_products:
                raise ValueError(MESSAGES.ERROR['OUT_OF_STOCK'])
    
            try:
                modal = PurchaseModal(
                    available_products,
                    self.balance_service,
                    self.product_service,
                    self.trx_manager
                )
                await interaction.response.send_modal(modal)
                response_sent = True
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="❌ Error",
                        description="Mohon coba lagi dalam beberapa saat",
                        color=COLORS.ERROR
                    ),
                    ephemeral=True
                )
    
        except ValueError as e:
            await self._send_error_response(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in buy callback: {e}")
            await self._send_error_response(
                interaction,
                MESSAGES.ERROR['TRANSACTION_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)
    @discord.ui.button(
        style=discord.ButtonStyle.secondary,
        label="📜 Riwayat",
        custom_id=BUTTON_IDS.HISTORY
    )
    async def history_callback(self, interaction: discord.Interaction, button: Button):
        if not await self.acquire_response_lock(interaction):
            try:
                await interaction.response.send_message(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            except discord.errors.InteractionResponded:
                await interaction.followup.send(
                    embed=discord.Embed(
                        title="⏳ Mohon Tunggu",
                        description=MESSAGES.INFO['COOLDOWN'],
                        color=COLORS.WARNING
                    ),
                    ephemeral=True
                )
            return
    
        try:
            response_sent = False
            
            # Check maintenance mode
            if await self.admin_service.is_maintenance_mode():
                raise ValueError(MESSAGES.INFO['MAINTENANCE'])
    
            # Defer response
            try:
                await interaction.response.defer(ephemeral=True)
                response_sent = True
            except discord.errors.InteractionResponded:
                response_sent = True
            except Exception:
                pass
    
            # Get user's GrowID
            growid_response = await self.balance_service.get_growid(str(interaction.user.id))
            if not growid_response.success:
                raise ValueError(growid_response.error)
    
            growid = growid_response.data
            if not growid:
                raise ValueError(MESSAGES.ERROR['NOT_REGISTERED'])
    
            # Get transaction history
            trx_response = await self.trx_manager.get_transaction_history(growid, limit=5)
            if not trx_response.success:
                raise ValueError(trx_response.error)
    
            transactions = trx_response.data
            if not transactions:
                raise ValueError(MESSAGES.ERROR['NO_HISTORY'])
    
            # Create embed
            embed = discord.Embed(
                title="📊 Riwayat Transaksi",
                description=f"Transaksi terakhir untuk `{growid}`",
                color=COLORS.INFO
            )
    
            # Add transaction details
            for i, trx in enumerate(transactions, 1):
                try:
                    embed.add_field(
                        name=f"{'💰' if 'DEPOSIT' in trx['type'] else '🛒' if 'PURCHASE' in trx['type'] else '💸'} Transaksi #{i}",
                        value=(
                            f"```yml\n"
                            f"Tipe: {trx['type']}\n"
                            f"Tanggal: {trx['date']}\n"
                            f"Perubahan: {trx['change']}\n"
                            f"Status: {trx['status']}\n"
                            f"Detail: {trx['details']}\n"
                            "```"
                        ),
                        inline=False
                    )
                except Exception:
                    continue
    
            embed.set_footer(text=f"Menampilkan {len(transactions)} transaksi terakhir")
            embed.timestamp = datetime.utcnow()
    
            # Send response
            if response_sent:
                await interaction.followup.send(embed=embed, ephemeral=True)
            else:
                await interaction.response.send_message(embed=embed, ephemeral=True)
    
        except ValueError as e:
            await self._send_error_response(interaction, str(e), response_sent)
        except Exception as e:
            self.logger.error(f"Error in history callback: {e}")
            await self._send_error_response(
                interaction,
                MESSAGES.ERROR['HISTORY_FAILED'],
                response_sent
            )
        finally:
            self.release_response_lock(interaction)
class LiveButtonManager(BaseLockHandler, BaseResponseHandler):
    def __init__(self, bot):
        if not hasattr(self, 'initialized') or not self.initialized:
            BaseLockHandler.__init__(self)
            BaseResponseHandler.__init__(self)
            
            self.bot = bot
            self.logger = logging.getLogger("LiveButtonManager")
            self.cache_manager = CacheManager()
            self.admin_service = AdminService(bot)
            self.stock_channel_id = int(self.bot.config.get('id_live_stock', 0))
            self.current_message = None
            self.stock_manager = None
            self._ready = asyncio.Event()
            self.initialized = True
            self.logger.info("LiveButtonManager initialized")

    def create_view(self):
        """Create shop view with buttons"""
        return ShopView(self.bot)

    async def set_stock_manager(self, stock_manager):
        """Set stock manager untuk integrasi"""
        self.stock_manager = stock_manager
        self._ready.set()
        self.logger.info("Stock manager set successfully")
        await self.force_update()

    async def get_or_create_message(self) -> Optional[discord.Message]:
        """Create or get existing message with both stock display and buttons"""
        async with self._lock:  # Menggunakan lock dari BaseLockHandler
            try:
                channel = self.bot.get_channel(self.stock_channel_id)
                if not channel:
                    self.logger.error(f"Channel stock dengan ID {self.stock_channel_id} tidak ditemukan")
                    return None

                # First check if stock manager has a valid message
                if self.stock_manager and self.stock_manager.current_stock_message:
                    self.current_message = self.stock_manager.current_stock_message
                    # Update buttons only
                    try:
                        view = self.create_view()
                        await self.current_message.edit(view=view)
                    except discord.errors.NotFound:
                        self.current_message = None
                    except Exception as e:
                        self.logger.error(f"Error updating view: {e}")
                    return self.current_message

                # Find last message if exists
                if self.stock_manager:
                    try:
                        existing_message = await self.stock_manager.find_last_message()
                        if existing_message:
                            self.current_message = existing_message
                            # Update both stock manager and button manager references
                            self.stock_manager.current_stock_message = existing_message

                            # Update embed and view
                            embed = await self.stock_manager.create_stock_embed()
                            view = self.create_view()
                            await existing_message.edit(embed=embed, view=view)
                            return existing_message
                    except Exception as e:
                        self.logger.error(f"Error finding last message: {e}")

                # Create new message if none found
                try:
                    if self.stock_manager:
                        embed = await self.stock_manager.create_stock_embed()
                    else:
                        embed = discord.Embed(
                            title="🏪 Live Stock",
                            description=MESSAGES.INFO['INITIALIZING'],
                            color=COLORS.WARNING
                        )

                    view = self.create_view()
                    self.current_message = await channel.send(embed=embed, view=view)

                    # Update stock manager reference
                    if self.stock_manager:
                        self.stock_manager.current_stock_message = self.current_message

                    return self.current_message
                except Exception as e:
                    self.logger.error(f"Error creating new message: {e}")
                    return None

            except Exception as e:
                self.logger.error(f"Error in get_or_create_message: {e}")
                return None

    async def force_update(self) -> bool:
        """Force update stock display and buttons"""
        try:
            async with asyncio.timeout(30):  # Tambahkan timeout
                async with self._lock:  # Menggunakan lock dari BaseLockHandler
                    if not self.current_message:
                        self.current_message = await self.get_or_create_message()

                    if not self.current_message:
                        return False

                    # Check maintenance mode 
                    try:
                        is_maintenance = await self.admin_service.is_maintenance_mode()
                        if is_maintenance:
                            embed = discord.Embed(
                                title="🔧 Maintenance Mode",
                                description=MESSAGES.INFO['MAINTENANCE'],
                                color=COLORS.WARNING
                            )
                            await self.current_message.edit(embed=embed, view=None)
                            return True
                    except Exception as e:
                        self.logger.error(f"Error checking maintenance mode: {e}")
                        return False

                    if self.stock_manager:
                        try:
                            await self.stock_manager.update_stock_display()
                        except Exception as e:
                            self.logger.error(f"Error updating stock display: {e}")

                    try:
                        view = self.create_view()
                        await self.current_message.edit(view=view)
                        return True
                    except discord.errors.NotFound:
                        self.current_message = None
                        return False
                    except Exception as e:
                        self.logger.error(f"Error updating view: {e}")
                        return False

        except asyncio.TimeoutError:
            self.logger.error("Force update timed out")
            return False
        except Exception as e:
            self.logger.error(f"Error in force update: {e}")
            return False

    async def cleanup(self):
        """Cleanup resources"""
        try:
            # Cleanup base handlers
            await super().cleanup()
            
            if self.current_message:
                try:
                    embed = discord.Embed(
                        title="🛠️ Maintenance",
                        description=MESSAGES.INFO['MAINTENANCE'],
                        color=COLORS.WARNING
                    )
                    await self.current_message.edit(embed=embed, view=None)
                except Exception as e:
                    self.logger.error(f"Error updating message during cleanup: {e}")

            # Clear caches
            patterns = [
                'live_stock_message_id',
                'world_info',
                'available_products'
            ]

            for pattern in patterns:
                try:
                    await self.cache_manager.delete(pattern)
                except Exception as e:
                    self.logger.error(f"Error clearing cache {pattern}: {e}")

            self.logger.info("LiveButtonManager cleanup completed")

        except Exception as e:
            self.logger.error(f"Error in cleanup: {e}")
class LiveButtonsCog(commands.Cog):
    """
    Live Buttons Manager with Shop Integration
    Version: 2.1.0
    Author: fdyytu2
    Created at: 2025-03-16 17:36:03 UTC
    """
    
    def __init__(self, bot):
        self.bot = bot
        self.button_manager = LiveButtonManager(bot)
        self.stock_manager = None
        self.logger = logging.getLogger("LiveButtonsCog")
        self._ready = asyncio.Event()
        self._initialization_lock = asyncio.Lock()
        self._cleanup_lock = asyncio.Lock()
        self.logger.info("LiveButtonsCog initialized")

    async def wait_for_stock_manager(self, timeout=30) -> bool:
        """Wait for stock manager to be available with improved error handling"""
        try:
            start_time = datetime.utcnow()
            self.logger.info("Waiting for StockManager...")

            while (datetime.utcnow() - start_time).total_seconds() < timeout:
                try:
                    stock_cog = self.bot.get_cog('LiveStockCog')
                    if stock_cog and hasattr(stock_cog, 'stock_manager'):
                        self.logger.info("Found StockManager")
                        self.stock_manager = stock_cog.stock_manager
                        
                        if self.stock_manager and self.stock_manager._ready.is_set():
                            self.logger.info("StockManager is ready")
                            return True
                except Exception as e:
                    self.logger.error(f"Error checking StockManager: {e}")

                await asyncio.sleep(2)

            self.logger.error("Timeout waiting for StockManager")
            return False

        except Exception as e:
            self.logger.error(f"Error waiting for stock manager: {e}")
            return False

    async def initialize_dependencies(self) -> bool:
        """Initialize all dependencies with improved error handling"""
        try:
            async with self._initialization_lock:
                self.logger.info("Starting dependency initialization...")

                if self._ready.is_set():
                    self.logger.info("Dependencies already initialized")
                    return True

                # Wait for bot to be ready
                if not self.bot.is_ready():
                    self.logger.info("Waiting for bot to be ready...")
                    await self.bot.wait_until_ready()

                # Verify required services
                required_services = [
                    'balance_manager_loaded',
                    'product_manager_loaded',
                    'transaction_manager_loaded'
                ]

                for service in required_services:
                    if not hasattr(self.bot, service):
                        self.logger.error(f"Missing required service: {service}")
                        return False

                # Wait for stock manager with timeout
                try:
                    async with asyncio.timeout(30):
                        if not await self.wait_for_stock_manager():
                            raise RuntimeError("Failed to initialize StockManager")
                except asyncio.TimeoutError:
                    self.logger.error("StockManager initialization timed out")
                    return False

                # Set stock manager to button manager
                try:
                    await self.button_manager.set_stock_manager(self.stock_manager)
                except Exception as e:
                    self.logger.error(f"Error setting stock manager: {e}")
                    return False

                self._ready.set()
                self.logger.info("Dependencies initialized successfully")
                return True

        except Exception as e:
            self.logger.error(f"Error initializing dependencies: {e}")
            return False

    async def cog_load(self):
        """Setup when cog is loaded with improved error handling"""
        try:
            self.logger.info("LiveButtonsCog loading...")

            # Initialize dependencies with timeout
            try:
                async with asyncio.timeout(45):
                    success = await self.initialize_dependencies()
                    if not success:
                        raise RuntimeError("Failed to initialize dependencies")
                    self.logger.info("Dependencies initialized successfully")
            except asyncio.TimeoutError:
                self.logger.error("Initialization timed out")
                raise RuntimeError("Initialization timed out")

            # Start background tasks
            self.check_display.start()
            self.logger.info("LiveButtonsCog loaded successfully")

        except Exception as e:
            self.logger.error(f"Error in cog_load: {e}")
            raise

    async def cog_unload(self):
        """Cleanup when cog is unloaded with improved error handling"""
        async with self._cleanup_lock:
            try:
                # Stop background tasks
                try:
                    self.check_display.cancel()
                except Exception as e:
                    self.logger.error(f"Error canceling check_display task: {e}")

                # Cleanup button manager
                try:
                    await self.button_manager.cleanup()
                except Exception as e:
                    self.logger.error(f"Error cleaning up button manager: {e}")

                # Clear event states
                self._ready.clear()

                self.logger.info("LiveButtonsCog unloaded successfully")

            except Exception as e:
                self.logger.error(f"Error in cog_unload: {e}")

    @tasks.loop(minutes=5.0)
    async def check_display(self):
        """Periodically check and update display with improved error handling"""
        if not self._ready.is_set():
            return

        try:
            async with asyncio.timeout(30):  # Add timeout
                message = self.button_manager.current_message
                if not message:
                    # Create new message if none exists
                    await self.button_manager.get_or_create_message()
                else:
                    # Update embed only, NOT view
                    if self.stock_manager:
                        try:
                            embed = await self.stock_manager.create_stock_embed()
                            await message.edit(embed=embed)
                        except discord.errors.NotFound:
                            self.button_manager.current_message = None
                            await self.button_manager.get_or_create_message()
                        except Exception as e:
                            self.logger.error(f"Error updating display: {e}")

        except asyncio.TimeoutError:
            self.logger.error("Display update timed out")
        except Exception as e:
            self.logger.error(f"Error in check_display: {e}")

    @check_display.before_loop
    async def before_check_display(self):
        """Wait until ready before starting the loop"""
        await self.bot.wait_until_ready()
        await self._ready.wait()

    @check_display.error
    async def check_display_error(self, error):
        """Handle errors in check_display task"""
        self.logger.error(f"Error in check_display task: {error}")
async def setup(bot):
    """Setup cog with proper error handling"""
    try:
        if not hasattr(bot, COG_LOADED['LIVE_BUTTONS']):
            # Verify required extensions
            required_extensions = [
                'ext.live_stock',
                'ext.balance_manager',
                'ext.product_manager',
                'ext.trx'
            ]

            for ext in required_extensions:
                if ext not in bot.extensions:
                    logging.info(f"Loading required extension: {ext}")
                    await bot.load_extension(ext)
                    await asyncio.sleep(2)  # Give time to initialize

            cog = LiveButtonsCog(bot)
            await bot.add_cog(cog)

            # Wait for initialization with timeout
            try:
                async with asyncio.timeout(45):
                    await cog._ready.wait()
            except asyncio.TimeoutError:
                logging.error("LiveButtonsCog initialization timed out")
                await bot.remove_cog('LiveButtonsCog')
                raise RuntimeError("Initialization timed out")

            setattr(bot, COG_LOADED['LIVE_BUTTONS'], True)
            logging.info("LiveButtons cog loaded successfully")

    except Exception as e:
        logging.error(f"Failed to load LiveButtonsCog: {e}")
        if hasattr(bot, COG_LOADED['LIVE_BUTTONS']):
            delattr(bot, COG_LOADED['LIVE_BUTTONS'])
        raise

async def teardown(bot):
    """Cleanup when unloading the cog"""
    try:
        cog = bot.get_cog('LiveButtonsCog')
        if cog:
            await bot.remove_cog('LiveButtonsCog')
        if hasattr(bot, COG_LOADED['LIVE_BUTTONS']):
            delattr(bot, COG_LOADED['LIVE_BUTTONS'])
        logging.info("LiveButtons cog unloaded successfully")
    except Exception as e:
        logging.error(f"Error unloading LiveButtonsCog: {e}")