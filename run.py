import argparse
import logging
import os
from pathlib import Path

from waitress import serve

from image_vault import create_app


def main():
    parser = argparse.ArgumentParser(description='Image Vault / 图仓')
    parser.add_argument('--host', default=os.environ.get('VAULT_HOST', '127.0.0.1'))
    parser.add_argument('--port', type=int, default=int(os.environ.get('VAULT_PORT', '8765')))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(name)s %(message)s')
    app = create_app()
    print(f'Image Vault: http://{args.host}:{args.port}', flush=True)
    print(f'Data directory: {Path(app.config["DATA_DIR"]).resolve()}', flush=True)
    try:
        serve(app, host=args.host, port=args.port, threads=8, max_request_body_size=65536, channel_timeout=60)
    finally:
        app.extensions['job_executor'].shutdown(wait=False, cancel_futures=True)


if __name__ == '__main__':
    main()
