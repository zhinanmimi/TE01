# Gunicorn configuration file
import multiprocessing
import os

# Server socket
bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"
worker_class = "aiohttp.worker.GunicornWebWorker"

# Worker processes
workers = multiprocessing.cpu_count()
threads = 2
timeout = 120 