#!/usr/bin/env python3
import os
import csv
import json
import socket
import logging
from functools import cache
from pathlib import Path
from typing import Dict

import openai
from openai import ChatCompletion
from aiohttp import web, ClientSession


MODEL = "gpt-3.5-turbo"
PROMPT = """You are a translate service. User will input HTML that may \
contain text in nature language and you will detect which language it is and \
translate it to <TARGET>. Keep HTML tags untouched and only translate the \
text part. Do not translate emoji code that surrounded by colons (e.g. \
:smile:). Do not translate Emoticon/Kaomoji. Return result in a valid JSON.

Example output:
{
    "detectedLanguage": {
        "confidence": 87,
        "language": "zh"
    },
    "translatedText": "<p>Hello!</p>"
}
"""

routes = web.RouteTableDef()


@cache
def languages_code_name() -> Dict[str, str]:
    with open('iso_639_1.csv', newline='') as f:
        reader = csv.reader(f, delimiter=',')
        return { code: name for code, name, _ in reader }


def generate_supported_languages():
    langs = []
    codes = []
    with open('iso_639_1.csv', newline='') as f:
        reader = csv.reader(f, delimiter=',')
        for code, name, _ in reader:
            codes.append(code)
            langs.append(dict(code=code, name=name, targets=codes))
    return langs


@routes.get('/')
async def index(_: web.Request) -> web.Response:
    return web.Response(text="It's running!")


@routes.get('/languages')
async def languages(_: web.Request) -> web.Response:
    code_name = languages_code_name()
    targets = list(code_name.keys())
    langs = [dict(code=code, name=name, targets=targets)
             for code, name in code_name.items()]
    return web.json_response(langs)


@routes.post('/translate')
async def translate(request: web.Request) -> web.Response:
    req = await request.json()
    text, target_code = req['q'], req['target']
    target = languages_code_name().get(target_code, target_code)
    chat = await ChatCompletion.acreate(
        model=MODEL,
        messages=[
            dict(role='system', content=PROMPT.replace('<TARGET>', target)),
            dict(role='user', content=text),
        ]
    )
    logging.debug(chat)
    resp = json.loads(chat.choices[0].message.content)
    detected_lang = resp['detectedLanguage']['language']
    logging.info(
        f'{detected_lang}/{target_code} '
        f'{chat.usage.prompt_tokens}+{chat.usage.completion_tokens} tokens'
    )
    return web.json_response(resp)


async def on_startup(_: web.Application):
    openai.aiosession.set(ClientSession())


async def on_cleanup(_: web.Application):
    session = openai.aiosession.get()
    if session is not None:
        await session.close()


async def init() -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    app.on_startup.append(on_startup)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    logging.basicConfig(format='[%(levelname)s] %(message)s', level=logging.INFO)

    sock = None
    if str(os.getpid()) == os.environ.get('LISTEN_PID'):
        logging.info('Systemd awared')
        fds = int(os.environ.get('LISTEN_FDS', 0))
        if fds:
            sock = socket.socket(fileno=3)
            logging.info('Use systemd-passed socket')

        keyfile = os.environ.get('CREDENTIALS_DIRECTORY', '') / Path('openai_key')
        if keyfile.exists():
            with keyfile.open() as f:
                openai.api_key = f.read().strip()
                logging.info(f'Load OpenAI API key from {keyfile}')

    web.run_app(init(), sock=sock)


if __name__ == '__main__':
    main()

