# Конфигурация — ключи зашифрованы через Fernet (PBKDF2 + пароль)
# Для перешифровки с новым паролем: python3 encrypt_config.py

# Telegram (зашифровано)
TG_TOKEN      = "gAAAAABqRoyEqfy1WQNsNnGlTnOGqCJav0uAL3JVdpj-NJyHneFxTvF-91sdlLR473yN-FiLiKHFPWygWUglAzOGI1XC_J4NR_WM9O6jFtUa6EMetnUFW8q0dtx04OSfQKMH6-IH6p4z"

# Anthropic (зашифровано)
ANTHROPIC_KEY = "gAAAAABqRoyEaNFSLvqsAUp-_6ex96xeMuvSvW17SO_AjgjZcILzLRFazXKofN4raqdKMhEs9037Y3OUkXbv4CwVeQsE0HEbkvwMVoVb_9FWhODtAi9BeKILyLVS1yX_dE_fFGpkZtCh"

# Firecrawl (зашифровано)
FIRECRAWL_KEY = "gAAAAABqRoyExC8c2XvLMOwotEOsPpk5lUYn7_jud_60qZVDacq9Fup1XLFZMzJJkvxNjTaNOKmQqAxWafJgUOzWmIjIS17YrMEMk5JRA3TwDDQgQa4ynxiYtX0aanRhpWxj9mXnCBXq"

# База данных
DB_PATH = "apartments.db"

# Прокси для Telegram API (нужен если api.telegram.org недоступен напрямую)
# Форматы:
#   SOCKS5: "socks5://user:pass@host:port"
#   HTTP:   "http://user:pass@host:port"
# Оставь пустой строкой если прокси не нужен
PROXY_URL = ""

# Если на сервере системный прокси мешает Firecrawl — установи True
# чтобы сбросить HTTP_PROXY/HTTPS_PROXY для всего процесса
DISABLE_SYSTEM_PROXY_FOR_FIRECRAWL = True

# Если сервер блокирует исходящие соединения к api.firecrawl.dev —
# укажи прокси через который Firecrawl будет ходить в интернет.
# Форматы: "socks5://host:port", "http://user:pass@host:port"
# Оставь пустым если прокси не нужен
FIRECRAWL_PROXY = ""
