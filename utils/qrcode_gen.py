"""
utils/qrcode_gen.py — تولید QR Code از لینک subscription
"""

from __future__ import annotations

import asyncio
import io
from functools import partial

import qrcode
from qrcode.image.pil import PilImage
from loguru import logger


async def generate_qr_code(sub_link: str, box_size: int = 10, border: int = 4) -> bytes:
    """
    تولید QR Code از لینک subscription و برگرداندن bytes تصویر PNG.

    Args:
        sub_link: لینک subscription که در QR Code قرار می‌گیرد
        box_size: اندازه هر خانه QR (پیکسل)
        border: حاشیه اطراف QR (تعداد خانه)

    Returns:
        bytes تصویر PNG
    """

    def _make_qr() -> bytes:
        qr = qrcode.QRCode(
            version=None,            # اندازه اتوماتیک
            error_correction=qrcode.constants.ERROR_CORRECT_L,
            box_size=box_size,
            border=border,
        )
        qr.add_data(sub_link)
        qr.make(fit=True)

        img: PilImage = qr.make_image(fill_color="black", back_color="white")

        buffer = io.BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    # اجرا در thread pool تا event loop بلاک نشود
    loop = asyncio.get_event_loop()
    png_bytes = await loop.run_in_executor(None, _make_qr)
    logger.debug(f"QR Code تولید شد — اندازه: {len(png_bytes)} بایت")
    return png_bytes
