# Конфигурация — заполни перед запуском

# Telegram
TG_TOKEN  = "8235227377:AAH15zuO5AxMAbmeMveSWLuyMVleCdWbixk"

# Anthropic (Claude)
ANTHROPIC_KEY = "sk-uzUMo0aRDip7x0zzfvrFbuhnRq4M3cdY"

# Firecrawl
FIRECRAWL_KEY = "fc-cee6019694b0425a9902a831c08ca32f"

# База данных
DB_PATH = "apartments.db"

# Прокси для Telegram API (нужен если api.telegram.org недоступен напрямую)
# Форматы:
#   SOCKS5: "socks5://user:pass@host:port"
#   HTTP:   "http://user:pass@host:port"
# Оставь пустой строкой если прокси не нужен
PROXY_URL = ""

# Если на сервере прокси мешает Firecrawl — установи True чтобы сбросить
# HTTP_PROXY/HTTPS_PROXY переменные для всего процесса
DISABLE_SYSTEM_PROXY_FOR_FIRECRAWL = True
