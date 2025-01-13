# ChatGPT Translate for Mastodon
Enable ChatGPT API-powered translation on your Mastodon instance!

A lightweight scirpt that act as LibreTranslate service but chat with GPT to
get translation done.

![Scooby doo mask reveal meme. Before reveal: LibreTranslate;
after: OpenAI.](/assets/meme.webp)

## Setup

Prerequisite: [openai-python](https://github.com/openai/openai-python)

Run the script:

```bash
export OPENAI_API_KEY=sk-xxxxxxxxxx
./chatlibre.py
```

Update Mastodon [environment variables](https://docs.joinmastodon.org/admin/config/#libre_translate_endpoint):

```
LIBRE_TRANSLATE_ENDPOINT=http://localhost:8080
LIBRE_TRANSLATE_API_KEY=whatever
```

(We don't check LibreTranslate API key, so this value doesn't matter)

Then restart your Mastodon web server.

## Run as systemd service

Clone this repo into `/opt/chatlibre`, then

```bash
cd /opt/chatlibre
cp systemd/* /etc/systemd/system/
echo sk-xxxxxxxxxx > openai_key
chmod og-r openai_key
```

Edit `chatlibre.service` if you use path other than `/opt/chatlibre`.

Edit `chatlibre.socket` to change listen address and port number.

```bash
systemctl daemon-reload
systemctl enable --now chatlibre.socket
```

