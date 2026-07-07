"""Compatibilidad asyncio: run_blocking funciona en Python 3.8+ (Ubuntu 20.04).

asyncio.to_thread existe recién en Python 3.9; en 3.8 se emula con run_in_executor.
"""
import asyncio
import functools

if hasattr(asyncio, "to_thread"):
    async def run_blocking(func, *args, **kwargs):
        """Ejecuta una función bloqueante en un thread sin frenar el event loop."""
        return await asyncio.to_thread(func, *args, **kwargs)
else:
    async def run_blocking(func, *args, **kwargs):
        """Ejecuta una función bloqueante en un thread sin frenar el event loop (py3.8)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))
