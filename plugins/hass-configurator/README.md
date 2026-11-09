# HASS Configurator / File Editor

Работайте в [клоне проекта на Raspberry Pi](../../readme.md#клонирование-репозитория). До запуска подготовьте `plugins/hass-configurator/data/`: в Git для него есть пустой `.stub`; рабочие файлы внутри игнорируются.

Сервис и профиль `hass-configurator` определены в [общем Compose](../docker-compose.yml); по умолчанию профиль выключен. В `plugins/.env` задайте `HC_USERNAME` и непустой `HC_PASSWORD`, включите профиль и выполните [общую инструкцию](../README.md#применение-и-отключение).

Собственные настройки редактора, включая `settings.conf`, находятся в `plugins/hass-configurator/data/` и монтируются в `/config`. Редактор открывает `${HA_DIR}/config` в `/hass-config`: это общий каталог работающего HA, а не копия для плагина. Для полного бэкапа сохраняйте и `data/` редактора, и `homeassistant/config/`.
