# Восстановление бэкапов с прежними именами каталогов

Эта справка относится к архивам `plugin-ИМЯ-МЕТКА.tar.gz` и `plugins-config-МЕТКА.tar.gz`, содержащим пути `plugins/...`, а также к конфигурации HA с симлинками на `/repos/...`. Текущие архивы используют `addons/...` и симлинки на `/integrations/...`.

1. Восстанавливайте выбранную копию при остановленных сервисах. Распакуйте архивы HA, нужных дополнений и общих настроек в отдельный пустой каталог от root с сохранением владельцев, прав, ACL и расширенных атрибутов (`tar --extract --gzip --same-owner --same-permissions --acls --xattrs --file АРХИВ --directory КАТАЛОГ`). Не распаковывайте их поверх работающей установки.

2. Перенесите восстановленные `plugins/ИМЯ/data/` в соответствующие `addons/ИМЯ/data/`, сохраняя владельцев и права. Перенесите локальный `.env` в `addons/.env`, замените имя `PLUGINS_BIND_IP` на `ADDONS_BIND_IP`. Используйте Compose и скрипты из текущего клона проекта; старый Compose из `plugins-config-*` нужен только как справка о прежних настройках. При совпадении каталогов сначала согласуйте их содержимое, без перезаписи существующих данных.

3. Скачайте ручные интеграции в `integrations/`, выбрав прежнюю совместимую версию. Если в архиве нет записи commit, определите версию по своим записям и сохраните её согласно [инструкции интеграций](../integrations/readme.md). Архив `plugin-yandex-smart-home-*` содержит только старую инструкцию; настройки Yandex находятся в архиве HA.

4. Проверьте ссылки в восстановленном `homeassistant/config/custom_components/`. Замените только симлинки с целями `/repos/...` на соответствующие `/integrations/...`, сохраняя остальную часть пути. Для Yandex Smart Home из корня проекта:

   ```bash
   link=homeassistant/config/custom_components/yandex_smart_home
   if [ "$(sudo readlink -- "$link")" = /repos/yandex_smart_home/custom_components/yandex_smart_home/ ] ||
      [ "$(sudo readlink -- "$link")" = /repos/yandex_smart_home/custom_components/yandex_smart_home ]; then
     sudo ln -sfnT /integrations/yandex_smart_home/custom_components/yandex_smart_home "$link"
   fi
   ```

5. Если на сервере остались контейнеры Compose-проекта `ha-plugins`, удалите их перед запуском `ha-addons`: имена сервисных контейнеров совпадают, а метки проектов различаются. При остановленном основном стеке выполните из текущего `addons/`:

   ```bash
   docker compose --env-file .env --project-name ha-plugins --profile '*' down --remove-orphans
   ```

   Команда сохраняет образы и данные; не добавляйте `--volumes`. Контейнеры других проектов новый контроллер не принимает в управление и не удаляет.

6. Проверьте `docker compose config --quiet` в `homeassistant/` и `bash system/addons.sh validate` из корня проекта. Запустите `bash system/ha.sh start`, затем `bash system/ha.sh check`. Проверьте доступ к компонентам из контейнера по [инструкции интеграций](../integrations/readme.md#ручная-установка).

Старые архивы и отчёты `schema_version: 1` сохраняют исходные имена. Новые отчёты используют `schema_version: 2`, `paths.addons` и `selected_addons`.
