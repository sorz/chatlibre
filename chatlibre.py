#!/usr/bin/env python3
import os
import csv
import json
import socket
import logging
import argparse
from pathlib import Path
from functools import cache
from dataclasses import dataclass
from typing import Dict, Any, TypedDict, Union

import openai
from openai import AsyncOpenAI
from aiohttp import web
from pydantic import BaseModel, ConfigDict, ValidationError, PositiveInt
from pydantic.alias_generators import to_camel


DEFAULT_MODEL = "gpt-4o-mini"
TAG_TARGET = "<TARGET>"
TAG_EXAMPLE = "<OUTPUT_EXAMPLE>"
PROMPT = f"""You are a translation service for fediverse posts. Given JSON \
array of text, detect its language and translate each text to {TAG_TARGET}. \
Keep HTML tags, emoji codes (e.g. `:smile:`), and emoticons intact. Provide \
the results in the following JSON format:

{TAG_EXAMPLE}"""


class DetectedLanguage(BaseModel):
    language: str
    confidence: PositiveInt


class Translation(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
    )

    detected_language: DetectedLanguage
    translated_text: list[str]


@dataclass
class Args:
    model: str
    disable_json_mode: bool
    disable_structured_output: bool
    listen_host: str
    listen_port: int
    log_level: str


class ResponseType(TypedDict):
    type: str


RESPONSE_FORMAT_TEXT = ResponseType(type="text")
RESPONSE_FORMAT_JSON = ResponseType(type="json_object")

routes = web.RouteTableDef()
key_openai_app = web.AppKey("key_openai", AsyncOpenAI)
key_args = web.AppKey("key_args", Args)


@cache
def languages_code_name() -> Dict[str, str]:
    with open("iso_639_1.csv", newline="", encoding="utf-8") as f:
        reader = csv.reader(f, delimiter=",")
        return {code: name for code, name, _ in reader}


def generate_supported_languages():
    langs = []
    codes = []
    with open("iso_639_1.csv", newline="") as f:
        reader = csv.reader(f, delimiter=",")
        for code, name, _ in reader:
            codes.append(code)
            langs.append(dict(code=code, name=name, targets=codes))
    return langs


EXAMPLE = Translation(
    detected_language=DetectedLanguage(
        language="zh",
        confidence=87,
    ),
    translated_text=["<p>Hello</p>", "Bye"],
)


def prompt(target_lang_code: str) -> str:
    lang = languages_code_name().get(target_lang_code, target_lang_code)
    example = EXAMPLE.model_dump_json(indent=2, by_alias=True)
    return PROMPT.replace(TAG_EXAMPLE, example).replace(TAG_TARGET, lang)


@routes.get("/")
async def index(_: web.Request) -> web.Response:
    return web.Response(text="It's running!")


@routes.get("/languages")
async def languages(_: web.Request) -> web.Response:
    code_name = languages_code_name()
    targets = list(code_name.keys())
    langs = [
        dict(code=code, name=name, targets=targets) for code, name in code_name.items()
    ]
    return web.json_response(langs)


async def chat(
    client: AsyncOpenAI,
    text: list[str] | str,
    target_code: str,
    model: str,
    response_format: Union[ResponseType, Translation],
) -> Dict[str, Any]:
    if isinstance(text, str):
        text_list = [text]
    else:
        text_list = text
    comp = await client.beta.chat.completions.parse(
        model=model,
        response_format=response_format,
        messages=[
            dict(role="system", content=prompt(target_code)),
            dict(role="user", content=json.dumps(text_list, ensure_ascii=False)),
        ],
    )
    logging.debug(comp)
    message = comp.choices[0].message
    resp = message.parsed or Translation.model_validate_json(message.content)
    detected_lang = resp.detected_language.language
    logging.info(
        f"{model} {detected_lang}/{target_code} "
        f"{comp.usage.prompt_tokens}+{comp.usage.completion_tokens} tokens"
    )
    resp = resp.model_dump(by_alias=True)
    if isinstance(text, str):
        resp["translated_text"] = resp["translated_text"][0]
    return resp


@routes.post("/translate")
async def translate(request: web.Request) -> web.Response:
    args = request.app[key_args]
    client = request.app[key_openai_app]
    req = await request.json()
    text, target_code = req["q"], req["target"]
    if isinstance(text, str):
        text = [text]
    if args.disable_json_mode:
        resp_format = RESPONSE_FORMAT_TEXT
    elif args.disable_structured_output:
        resp_format = RESPONSE_FORMAT_JSON
    else:
        resp_format = Translation
    try:
        resp = await chat(client, text, target_code, args.model, resp_format)
        return web.json_response(resp)
    except openai.RateLimitError as err:
        logging.warning(f"OpenAI rate limit: {err}")
        raise web.HTTPTooManyRequests(text="Upstream rate limit")
    except openai.OpenAIError as err:
        logging.warning(f"OpenAI error: {err}")
        raise web.HTTPServiceUnavailable(text="Upstream API error")
    except IOError as err:
        logging.warning(f"OpenAI API I/O error: {err}")
        raise web.HTTPServiceUnavailable(text="Upstream I/O error")
    except (ValidationError, json.JSONDecodeError, KeyError) as err:
        logging.warning(f"Decoding error: {err}")
        raise web.HTTPServiceUnavailable(text="Upstream response decoding error")


async def on_cleanup(app: web.Application):
    del app[key_openai_app]


async def init(args: Args, openai_api_key: str | None) -> web.Application:
    app = web.Application()
    app.add_routes(routes)
    app[key_args] = args
    app[key_openai_app] = AsyncOpenAI(api_key=openai_api_key)
    app.on_cleanup.append(on_cleanup)
    return app


def main():
    parser = argparse.ArgumentParser(
        prog="chatlibre",
        description="OpenAI-backed translation service for Mastodon instance",
    )
    parser.add_argument(
        "-m", "--model", default=DEFAULT_MODEL, help=f"default to {DEFAULT_MODEL}"
    )
    parser.add_argument(
        "--disable-structured-output",
        action="store_true",
        help="for old models which do not support structured output",
    )
    parser.add_argument(
        "--disable-json-mode",
        action="store_true",
        help="for even older models, imply --disable-structured-output",
    )
    parser.add_argument(
        "-l",
        "--listen-host",
        default="::",
        help="listen address of HTTP, ignored if systemd-passed socket detected",
    )
    parser.add_argument(
        "-p",
        "--listen-port",
        default=8080,
        type=int,
        help="listen TCP port of HTTP service",
    )
    parser.add_argument(
        "--log-level",
        default="info",
        choices=["debug", "info", "warning", "error"],
        help="default to info",
    )
    args = Args(**vars(parser.parse_args()))
    logging.basicConfig(
        format="[%(levelname)s] %(message)s", level=args.log_level.upper()
    )

    sock = None
    if str(os.getpid()) == os.environ.get("LISTEN_PID"):
        logging.info("Systemd awared")
        fds = int(os.environ.get("LISTEN_FDS", 0))
        if fds:
            sock = socket.socket(fileno=3)
            logging.info("Use systemd-passed socket")

    openai_api_key = None
    keyfile = os.environ.get("CREDENTIALS_DIRECTORY", "") / Path("openai_key")
    if keyfile.exists():
        with keyfile.open() as f:
            openai_api_key = f.read().strip()
            logging.info(f"Load OpenAI API key from {keyfile}")

    host = args.listen_host if sock is None else None
    port = args.listen_port if sock is None else None
    web.run_app(init(args, openai_api_key), sock=sock, host=host, port=port)


if __name__ == "__main__":
    main()
