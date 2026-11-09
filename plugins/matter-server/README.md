# Matter Server

Сервис и профиль `matter-server` находятся в [общем Compose плагинов](../docker-compose.yml). По умолчанию профиль выключен, образ не скачивается и контейнер не создаётся. Данные хранятся в `plugins/matter-server/data/` и монтируются в `/data`; бэкап всего `plugins/` включает контроллер, сертификаты и сведения о сопряжённых устройствах.

## Подготовка и включение

Используйте [клон проекта на Raspberry Pi](../../readme.md#клонирование-репозитория). Каталог `plugins/matter-server/data/` должен существовать до запуска; в клоне он создаётся благодаря пустому `.stub`. Для существующей установки сохраните перенесённые данные контроллера.

1. В `plugins/.env` задайте `MATTER_PRIMARY_INTERFACE` и `MATTER_BLUETOOTH_ADAPTER`: интерфейс с рабочим IPv6 и индекс адаптера BlueZ (`hci0` → `0`). При необходимости выберите версию или digest в `MATTER_SERVER_IMAGE`; по умолчанию сохранён образ `ghcr.io/home-assistant-libs/python-matter-server:stable`.
2. Подготовьте сертификаты по [инструкции ниже](#ручная-загрузка-сертификатов-при-недоступности-dcl-из-рф) до первого запуска: обращение к DCL в этом Compose заблокировано.
3. Добавьте `matter-server` к `COMPOSE_PROFILES` в `plugins/.env`. Например, `COMPOSE_PROFILES=matter-server` включает только Matter; `COMPOSE_PROFILES=zigbee2mqtt,hass-configurator,matter-server` — все три плагина.
4. После запуска основного стека выполните:

   ```bash
   bash /home/hauser/home_assistant_config/system/plugins.sh apply /home/hauser/home_assistant_config/plugins
   bash /home/hauser/home_assistant_config/system/plugins.sh check /home/hauser/home_assistant_config/plugins
   ```

5. [Добавьте интеграцию Matter в HA](#включение-интеграции-matter).

Контейнер использует `network_mode: host` с доступом к D-Bus и USB. Порт `5580` слушает непосредственно на хосте: `PLUGINS_BIND_IP` и переназначение опубликованных портов на него не распространяются. Дальнейшие команды обслуживания предназначены для включённого профиля и уже скачанного образа.

## Сеть и параметры запуска

Интеграция Matter в HA подключается к `ws://localhost:5580/ws`. Текущие аргументы запуска Matter Server:

```yaml
command:
  - --storage-path
  - /data
  - --paa-root-cert-dir
  - /data/credentials
  - --ota-provider-dir
  - /data/updates
  - --primary-interface
  - ${MATTER_PRIMARY_INTERFACE:-wlan0}
  - --bluetooth-adapter
  - "${MATTER_BLUETOOTH_ADAPTER:-0}"
```

`MATTER_PRIMARY_INTERFACE` выбирает сетевой интерфейс Matter. `MATTER_BLUETOOTH_ADAPTER` задаёт индекс BlueZ: например, `0` соответствует `hci0`; номера зависят от адаптеров хоста. Служба BlueZ работает на хосте; доступ к ней оба контейнера получают через `/run/dbus`. Один адаптер может использоваться Matter Server и интеграцией Bluetooth HA. Дополнительно смонтирован `/dev/bus/usb`.

Контейнеров Thread Border Router / OTBR в текущем Compose нет.

## Включение интеграции Matter

Home Assistant и Matter Server работают в режиме `host` на одном Raspberry Pi, поэтому HA может подключаться к серверу через `localhost`.

1. Откройте **Настройки → Устройства и службы** в Home Assistant.
2. Если появилась карточка **«Обнаружено: Matter»**, нажмите **Настроить**.
3. Если карточки нет, нажмите **Добавить интеграцию**, найдите **Matter** и выберите её.
4. Для подключения к уже запущенному серверу укажите **`ws://localhost:5580/ws`**. Если мастер предлагает использовать приложение Matter Server из Home Assistant OS, отключите этот вариант и перейдите к вводу адреса внешнего сервера.
5. Завершите настройку и убедитесь, что интеграция подключилась без ошибок.

Режим `host` позволяет обращаться к серверу по этому адресу; появление карточки автообнаружения зависит от обнаружения сервиса в сети. Порядок подключения внешнего сервера описан в [документации интеграции Matter](https://www.home-assistant.io/integrations/matter/).

## Ручная загрузка сертификатов при недоступности DCL из РФ

В текущем Compose доступ к `on.dcl.csa-iot.org` намеренно заблокирован. Сертификаты PAA хранятся на хосте в `/home/hauser/home_assistant_config/plugins/matter-server/data/credentials/`; контейнер видит этот каталог как `/data/credentials` благодаря параметру `--paa-root-cert-dir`.

Остановите Matter Server и подготовьте каталоги на Raspberry Pi:

```bash
cd /home/hauser/home_assistant_config/plugins
docker compose --profile matter-server stop --timeout 60 matter-server
sudo mkdir -p ./matter-server/data/credentials
mkdir -p /home/hauser/paa-root-certs
```

Скачайте файлы сертификатов `.der` из каталога [production/paa-root-certs проекта connectedhomeip](https://github.com/project-chip/connectedhomeip/tree/master/credentials/production/paa-root-certs) через **Download raw file**. Если GitHub недоступен с Raspberry Pi, скачайте файлы на другом компьютере и передайте их по SCP/SFTP в `/home/hauser/paa-root-certs/`.

Убедитесь, что загруженные `.der` уже находятся в указанном каталоге. Скопируйте их в хранилище и создайте служебный файл `.version` только после успешного копирования:

```bash
cd /home/hauser/home_assistant_config/plugins
sudo cp /home/hauser/paa-root-certs/*.der ./matter-server/data/credentials/ &&
  printf '1\n' | sudo tee ./matter-server/data/credentials/.version > /dev/null
```

В [Matter Server 8.1.0](https://github.com/home-assistant-libs/python-matter-server/blob/8.1.0/matter_server/server/helpers/paa_certificates.py) каталог без `.version` считается устаревшим, и его файлы удаляются перед загрузкой сертификатов. Сохранение этого файла позволяет использовать сертификаты, добавленные вручную. Тег `stable` может указывать на другую версию: при выборе образа проверяйте его механизм загрузки сертификатов.

После успешного копирования включите профиль в `.env`, примените его и проверьте журнал:

```bash
bash /home/hauser/home_assistant_config/system/plugins.sh apply /home/hauser/home_assistant_config/plugins
docker logs --tail 50 matter-server
```

Ошибки обращения к заблокированному DCL могут оставаться в журнале; проверьте, что сервер загрузил локальные сертификаты и запустился.

## Перезапуск Matter

```bash
docker restart matter-server
```

Перезапуск сохраняет данные в `plugins/matter-server/data/` и сопряжённые устройства.

## Логи Matter

Последние 50 строк с просмотром новых сообщений в реальном времени:

```bash
docker logs -f --tail 50 matter-server
```

Для выхода из просмотра нажмите **Ctrl+C**. Контейнер продолжит работать.

Весь доступный журнал контейнера:

```bash
docker logs matter-server
```

## Пересоздание контейнера Matter с сохранением устройств

```bash
cd /home/hauser/home_assistant_config/plugins
docker compose --profile matter-server up -d --pull never --no-deps --force-recreate matter-server
```

Compose пересоздаст только Matter Server. Каталог `plugins/matter-server/data/`, сертификаты и данные сопряжения сохранятся.

## Полный сброс Matter

Команда `sudo rm -rf ./matter-server/data` удаляет данные Matter-контроллера, включая его ключи, сведения о сопряжённых устройствах и сертификаты. После такого сброса устройства потребуется заново добавить в Home Assistant.

Для полного сброса с возможностью восстановления остановите и удалите контейнер, затем перенесите хранилище в резервную копию:

```bash
cd /home/hauser/home_assistant_config/plugins
docker compose --profile matter-server stop --timeout 60 matter-server
docker compose --profile matter-server rm -f matter-server

backup_dir="backups/matter-reset-$(date +%Y%m%dT%H%M%S%z)"
mkdir -p backups
mkdir -m 700 "$backup_dir"
sudo mv ./matter-server/data "$backup_dir/data"
```

После успешного переноса создайте новое хранилище и верните в него только сертификаты. Это нужно, поскольку доступ к DCL заблокирован. Если каталога `credentials` в резервной копии нет, загрузите сертификаты по инструкции выше перед запуском сервера. Выполняйте следующие команды в том же терминале, где задана переменная `backup_dir`:

```bash
test -d "$backup_dir/data/credentials" &&
  sudo mkdir -p ./matter-server/data &&
  sudo cp -a "$backup_dir/data/credentials" ./matter-server/data/ &&
  printf '1\n' | sudo tee ./matter-server/data/credentials/.version > /dev/null &&
  docker compose --profile matter-server up -d --pull never --no-deps matter-server
docker logs --tail 50 matter-server
```

При отсутствии резервного каталога сертификатов цепочка команд не запустит контейнер: сначала загрузите сертификаты по инструкции выше. Прежние данные контроллера сохранятся в `$backup_dir/data/`; новый контейнер создаст отдельную Matter-сеть.

