# Архитектура Home Assistant

Публичный пример конфигурации Home Assistant Container и инструкция по обслуживанию. Адреса из `192.0.2.0/24`, пользователь `hauser`, имя `ha-server` и пути ниже — примеры: замените их своими значениями перед выполнением команд. Реальные адреса, пароли, ключи и рабочие данные в репозиторий не входят.

## Оглавление

- [Сервер и локальная сеть](#сервер-и-локальная-сеть)
- [Установка и настройка Docker](#установка-и-настройка-docker)
  - [Подготовка пакетов](#подготовка-пакетов)
  - [Подключение репозитория и установка Docker](#подключение-репозитория-и-установка-docker)
  - [Доступ пользователя к Docker](#доступ-пользователя-к-docker)
  - [Настройка зеркал Docker Hub](#настройка-зеркал-docker-hub)
  - [Применение настроек и проверка](#применение-настроек-и-проверка)
- [Подготовка и первый запуск](#подготовка-и-первый-запуск)
  - [Копирование файлов на сервер](#копирование-файлов-на-сервер)
  - [Переменные основного стека](#переменные-основного-стека)
  - [Рабочие каталоги и Mosquitto](#рабочие-каталоги-и-mosquitto)
  - [Первый запуск сервисов](#первый-запуск-сервисов)
- [Сервисы и связи](#сервисы-и-связи)
  - [Zigbee и MQTT](#zigbee-и-mqtt)
  - [Matter и Bluetooth](#matter-и-bluetooth)
    - [Включение интеграции Matter](#включение-интеграции-matter)
    - [Ручная загрузка сертификатов при недоступности DCL из РФ](#ручная-загрузка-сертификатов-при-недоступности-dcl-из-рф)
    - [Перезапуск Matter](#перезапуск-matter)
    - [Логи Matter](#логи-matter)
    - [Пересоздание контейнера Matter с сохранением устройств](#пересоздание-контейнера-matter-с-сохранением-устройств)
    - [Полный сброс Matter](#полный-сброс-matter)
  - [Прямые интеграции HA](#прямые-интеграции-ha)
- [Хранение данных](#хранение-данных)
  - [Перенос хранилища Matter](#перенос-хранилища-matter)
- [Известные особенности текущей конфигурации](#известные-особенности-текущей-конфигурации)
- [Плагины Home Assistant](#плагины-home-assistant)
- [Инструменты сервера](#инструменты-сервера)
- [Автозапуск после перезагрузки](#автозапуск-после-перезагрузки)
- [Обслуживание](#обслуживание)
  - [Обновление существующей установки](#обновление-существующей-установки)
- [Проверка перед публикацией](#проверка-перед-публикацией)

## Сервер и локальная сеть

Пример рассчитан на Raspberry Pi с Debian 13 (trixie), Docker Engine и Docker Compose. Home Assistant запускается как **Home Assistant Container**, без отдельного Supervisor.

| Параметр | Пример |
| --- | --- |
| Имя хоста | `ha-server` |
| Рабочий каталог на сервере | `/home/hauser/homeassistant` |
| SSH | `ssh hauser@192.0.2.10` |
| Веб-интерфейс HA | <http://192.0.2.10:8123> |
| Шлюз локальной сети | `192.0.2.1` |
| Ethernet | `eth0`, `192.0.2.10/24` |
| Wi-Fi | `wlan0`, `192.0.2.11/24` |

Это условные адреса для документации. Выберите реальные интерфейсы, маршруты и адреса своей сети; для Matter нужен IPv6 в локальной сети. Интерфейс Matter и индекс Bluetooth-адаптера задаются в локальном `.env`.

Локальная копия [homeassistant/docker-compose.yml](homeassistant/docker-compose.yml) сама по себе не меняет конфигурацию работающего сервера. Перед применением скопируйте её в свой рабочий каталог и проверьте параметры.

Zigbee2MQTT и File Editor вынесены в [plugins/](plugins/README.md) и по умолчанию отключены. File Editor использует встроенную авторизацию; HTTP-соединение не шифруется. Yandex Smart Home устанавливается через [HACS](plugins/yandex-smart-home/README.md). Cloud Commander в стек не входит, Uptime Kuma отключена. Для администрирования предусмотрены Portainer и Cockpit, подробнее в [tools/README.md](tools/README.md).

## Установка и настройка Docker

Команды рассчитаны на новую установку Docker Engine на Debian 13 (trixie) и выполняются на Raspberry Pi под обычным пользователем с доступом к `sudo`. Порядок установки: [документация Docker для Debian](https://docs.docker.com/engine/install/debian/). Исходный материал: [статья об установке Docker](https://ru.linux-console.net/?p=39870).

### Подготовка пакетов

```bash
sudo apt update
sudo apt install ca-certificates curl gnupg openssl nano python3
```

Пакет `gnupg` нужен для `gpg`, `nano` — для редактирования локальных настроек, `openssl` — для создания пароля Portainer по инструкции инструментов, `python3` — для управления профилями плагинов (только стандартная библиотека). Команды SSH/SCP ниже предполагают, что SSH-сервер Raspberry Pi уже доступен.

### Подключение репозитория и установка Docker

Добавьте ключ Docker и репозиторий для `trixie`:

```bash
sudo install -d -m 755 /usr/share/keyrings
curl -fsSL https://download.docker.com/linux/debian/gpg | sudo gpg --dearmor -o /usr/share/keyrings/docker.gpg
sudo chmod a+r /usr/share/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/docker.gpg] https://download.docker.com/linux/debian trixie stable" | sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt update
sudo apt install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
```

Включите автозапуск Docker, запустите службу и проверьте её состояние и наличие Compose:

```bash
sudo systemctl enable --now docker
sudo systemctl is-active docker
sudo docker compose version
```

Состояние службы должно быть `active`. Для работы с Compose используется команда `docker compose`.

### Доступ пользователя к Docker

Добавьте текущего пользователя в группу `docker`:

```bash
sudo usermod -aG docker "${USER}"
```

Права вступят в силу после полного выхода из SSH-сеанса и повторного подключения либо после перезагрузки устройства. Для применения в текущем терминале можно выполнить `newgrp docker`. См. [настройку доступа в документации Docker](https://docs.docker.com/engine/install/linux-postinstall/).

### Настройка зеркал Docker Hub

Откройте конфигурацию Docker:

```bash
sudo install -d -m 755 /etc/docker
sudo nano /etc/docker/daemon.json
```

Если файла ещё нет, создайте его со следующим содержимым. В существующем файле добавьте или обновите только `registry-mirrors`, сохранив остальные параметры и корректный синтаксис JSON:

```json
{
  "registry-mirrors": [
    "https://mirror.gcr.io",
    "https://docker.m.daocloud.io",
    "https://dockerhub.timeweb.cloud"
  ]
}
```

Адреса зеркал указаны в документации [Google Cloud](https://cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images), [DaoCloud](https://github.com/DaoCloud/public-image-mirror#加速-docker) и [Timeweb Cloud](https://dockerhub.timeweb.cloud/). Это пример списка; доступность зеркал и нужных образов зависит от сети и ограничений провайдеров.

Параметр `registry-mirrors` применяется к [Docker Hub](https://docs.docker.com/docker-hub/image-library/mirror/). Образы Home Assistant и Matter Server из `ghcr.io` продолжают загружаться из своего реестра.

### Применение настроек и проверка

Перед перезапуском проверьте конфигурацию:

```bash
sudo dockerd --validate --config-file=/etc/docker/daemon.json
```

При результате `configuration OK` перезапустите Docker. Перезапуск на работающем сервере может прервать работу контейнеров.

```bash
sudo systemctl restart docker
sudo systemctl is-active docker
```

Команда `sudo systemctl daemon-reload` нужна перед перезапуском, если изменялись unit-файлы systemd или их overrides. Для изменения только `daemon.json` достаточно перезапуска Docker. Проверка конфигурации описана в [справочнике `dockerd`](https://docs.docker.com/reference/cli/dockerd/#daemon-configuration-file).

При необходимости перезагрузите устройство; это также применит членство пользователя в группе `docker`:

```bash
sudo reboot
```

После повторного подключения проверьте доступ без `sudo`, список зеркал в разделе `Registry Mirrors` и загрузку тестового образа:

```bash
docker info
docker compose version
docker run --rm hello-world
```

Успешный запуск `hello-world` проверяет загрузку и запуск контейнера; список `Registry Mirrors` показывает применённую конфигурацию, но сам по себе не подтверждает доступность каждого зеркала.

## Подготовка и первый запуск

В репозитории хранятся публичные шаблоны. Рабочие `.env`, конфигурации интеграций, пароли, сертификаты и базы создаются на сервере по шагам ниже и исключены из Git. На существующей установке сохраните свои рабочие файлы и выполните только необходимые шаги; не создавайте новую Zigbee- или Matter-сеть поверх действующей.

Основной стек находится в `homeassistant/`, контейнерные плагины — в `plugins/`, инструменты — в `tools/`: на сервере это соседние каталоги `/home/hauser/homeassistant`, `/home/hauser/plugins` и `/home/hauser/tools`. Каждый Compose-проект использует свой локальный `.env`. Для основной конфигурации нужен Docker Compose v2.27+; плагины также требуют Python 3.9+. Общая документация, примеры `examples/` и скрипты `system/` остаются в корне репозитория.

### Копирование файлов на сервер

Для действующей установки перед заменой Compose выполните подготовку из [инструкции миграции плагинов](plugins/README.md#миграция-из-основного-compose), используя прежний Compose.

Следующий блок выполняется **на компьютере, в корне репозитория**. Файлы из `homeassistant/` передаются непосредственно в каталог основного стека на сервере. Документация, примеры и скрипт автозапуска копируются туда отдельно.

```bash
ssh hauser@192.0.2.10 'mkdir -p /home/hauser/homeassistant/examples /home/hauser/homeassistant/system /home/hauser/plugins/yandex-smart-home /home/hauser/plugins/zigbee2mqtt /home/hauser/plugins/hass-configurator'
scp homeassistant/docker-compose.yml homeassistant/.env.example hauser@192.0.2.10:/home/hauser/homeassistant/
scp .gitignore readme.md hauser@192.0.2.10:/home/hauser/homeassistant/
scp examples/mosquitto.conf.example hauser@192.0.2.10:/home/hauser/homeassistant/examples/
scp system/autostart.sh system/plugins.sh system/plugins.py hauser@192.0.2.10:/home/hauser/homeassistant/system/
scp plugins/docker-compose.yml plugins/.env.example plugins/README.md hauser@192.0.2.10:/home/hauser/plugins/
scp plugins/yandex-smart-home/README.md hauser@192.0.2.10:/home/hauser/plugins/yandex-smart-home/
scp plugins/zigbee2mqtt/README.md hauser@192.0.2.10:/home/hauser/plugins/zigbee2mqtt/
scp plugins/hass-configurator/README.md hauser@192.0.2.10:/home/hauser/plugins/hass-configurator/
```

Далее подключитесь к Raspberry Pi:

```bash
ssh hauser@192.0.2.10
```

Остальные команды раздела выполняются **в SSH-сеансе на Raspberry Pi**. В примерах основной стек лежит в `/home/hauser/homeassistant`. Конфигурации инструментов копируются отдельно по [tools/README.md](tools/README.md#копирование-конфигурации-на-сервер).

### Переменные основного стека

Создайте `.env` из существующего в репозитории [homeassistant/.env.example](homeassistant/.env.example). После копирования на сервер оба файла находятся в каталоге основного стека. Команда сохраняет уже существующий `.env`:

```bash
cd /home/hauser/homeassistant
[ -e .env ] || (umask 077; cp .env.example .env)
chmod 600 .env
nano .env
```

| Переменная в `.env` основного стека | Назначение | Значение в шаблоне |
| --- | --- | --- |
| `MATTER_PRIMARY_INTERFACE` | Сетевой интерфейс Matter | `wlan0` |
| `MATTER_BLUETOOTH_ADAPTER` | Индекс адаптера BlueZ: `hci0` → `0`, `hci1` → `1` | `0` |
| `HA_BACKEND_NETWORK` | Общая сеть Mosquitto и контейнерных плагинов | `ha-backend` |

Имена интерфейсов, Bluetooth-адаптеров и путь координатора посмотрите на сервере:

```bash
ip -br link
ls /sys/class/bluetooth/
ls -l /dev/serial/by-id/
```

В основном `.env` укажите свои параметры Matter и имя общей сети. Если нет `hci*`, проверьте наличие Bluetooth-адаптера. Путь координатора и пароль File Editor задаются отдельно в `plugins/.env` и используются только соответствующим приложением; без Zigbee2MQTT USB-координатор не нужен. Имя `HA_BACKEND_NETWORK` должно совпадать у основного стека и плагинов.

На уже настроенном сервере после изменения `.env` нужно пересоздать соответствующий контейнер; простой `docker restart` не перечитывает переменные Compose. Для новой установки сначала выполните следующие разделы подготовки. Например, после изменения параметров Matter на работающем сервере:

```bash
cd /home/hauser/homeassistant
docker compose config --quiet
docker compose up -d --no-deps matter-server
```

Изменения параметров контейнерных плагинов применяются через `system/plugins.sh apply`, как описано в [инструкции плагинов](plugins/README.md#применение-и-отключение).

### Рабочие каталоги и Mosquitto

Команды выполняются на сервере из каталога основного стека. Файл `mosquitto.conf` создаётся из [публичного шаблона](examples/mosquitto.conf.example); существующая конфигурация сохраняется:

```bash
cd /home/hauser/homeassistant
mkdir -p config matter-data
mkdir -p mosquitto/config mosquitto/data mosquitto/log
[ -e mosquitto/config/mosquitto.conf ] || cp examples/mosquitto.conf.example mosquitto/config/mosquitto.conf
```

На **новой установке** создайте пользователя MQTT, например `mqttuser`. Следующая команда спросит пароль интерактивно; используйте его позже в Zigbee2MQTT и интеграции MQTT Home Assistant. Параметр `-c` создаёт новый файл паролей, поэтому команда выполняется только при его отсутствии:

```bash
cd /home/hauser/homeassistant
if [ ! -e mosquitto/config/passwd ]; then
  docker compose run --rm --no-deps --user 0:0 mosquitto mosquitto_passwd -c /mosquitto/config/passwd mqttuser
fi
sudo chown -R 1883:1883 mosquitto
sudo chmod 600 mosquitto/config/passwd
```

В официальном образе Mosquitto используется UID/GID `1883`; при выборе другого образа проверьте его пользователя. Для добавления учётной записи в существующий файл используйте `mosquitto_passwd` без `-c`, предварительно сохранив резервную копию. В Git пароль, его хеш и рабочая конфигурация не добавляются. Параметры брокера описаны в [справочнике Mosquitto](https://mosquitto.org/man/mosquitto-conf-5.html).

### Первый запуск сервисов

На сервере сначала проверьте Compose и запустите брокер и HA:

```bash
cd /home/hauser/homeassistant
docker compose config --quiet
docker compose up -d mosquitto homeassistant
docker compose ps
```

Home Assistant создаст рабочие файлы в `config/`. Откройте `http://АДРЕС_СЕРВЕРА:8123`, создайте учётную запись и добавьте интеграцию MQTT: брокер `127.0.0.1`, порт `1883`, созданные ранее пользователь и пароль. Mosquitto создаёт общую сеть `ha-backend`.

Контейнерные плагины по умолчанию выключены. Для Zigbee2MQTT или File Editor создайте отдельный `plugins/.env`, выберите профили и выполните [инструкцию плагинов](plugins/README.md#применение-и-отключение). Основной Compose больше не запускает эти контейнеры и не скачивает их образы. Для Yandex Smart Home используйте [инструкцию HACS](plugins/yandex-smart-home/README.md).

Matter запускается отдельно после [подготовки сертификатов](#ручная-загрузка-сертификатов-при-недоступности-dcl-из-рф), поскольку в приведённом Compose доступ к DCL заблокирован. Проверьте `MATTER_PRIMARY_INTERFACE` и `MATTER_BLUETOOTH_ADAPTER` до его запуска. После запуска сервера [добавьте интеграцию Matter в HA](#включение-интеграции-matter).

## Сервисы и связи

```mermaid
flowchart LR
    Browser["Браузер / приложение HA"] -->|"HTTP :8123"| HA["Home Assistant"]
    Zigbee["Zigbee-устройства"] <-->|Zigbee| USB["USB-координатор"]
    USB <-->|"/dev/ttyUSB0"| Z2M["Zigbee2MQTT · профиль plugins"]
    Z2M <-->|"MQTT: mosquitto:1883"| MQTT["Mosquitto"]
    HA <-->|"MQTT: 127.0.0.1:1883"| MQTT
    HA <-->|"WebSocket: localhost:5580/ws"| Matter["Matter Server"]
    Matter <-->|"IPv6 / локальная сеть"| MatterDevices["Matter-устройства"]
    HA <-->|"Native API / LAN"| ESP["ESPHome-устройства"]
    HA <-->|"D-Bus / BlueZ"| BT["Bluetooth hci0"]
    Matter <-->|"D-Bus / BlueZ; commissioning"| BT
    Config["Конфигурация HA"]
    Configurator["File Editor :3218 · профиль plugins"] -->|"общий каталог config"| Config
    HA --> Config
```

| Контейнер | Образ | Сеть и доступ | Функция |
| --- | --- | --- | --- |
| `homeassistant` | `ghcr.io/home-assistant/home-assistant:stable` | `host`, TCP `8123` | Интерфейс, автоматизации и интеграции HA |
| `mosquitto` | `eclipse-mosquitto:latest` | Сеть Compose; опубликован TCP `1883`, также проброшен `9001` | MQTT-брокер |
| `zigbee2mqtt` | `koenkk/zigbee2mqtt:2.14.3` | Общая сеть `ha-backend`; по умолчанию `127.0.0.1:8080` | Необязательный профиль в `plugins/`, Zigbee ↔ MQTT |
| `matter-server` | `ghcr.io/home-assistant-libs/python-matter-server:stable` | `host`, TCP `5580` | Отдельный Matter-контроллер; [веб-интерфейс](http://192.0.2.10:5580) |
| `hass-configurator` | `causticlab/hass-configurator-docker:0.6.0` | Сеть плагинов; по умолчанию `127.0.0.1:3218`, авторизация | Необязательный профиль в `plugins/`, File Editor для HA |

Основной Compose содержит три сервиса, Compose плагинов — два необязательных. У всех задано `restart: unless-stopped`. `homeassistant` зависит от запуска `mosquitto` через `depends_on`. Основной стек запускается перед плагинами и создаёт общую сеть; MQTT-подключение настраивается в Zigbee2MQTT. Скрипт управления не содержит проверок настроек отдельных приложений. Healthcheck приложений в Compose не задан.

Теги `latest` и `stable` основного стека меняются со временем и не фиксируют версии приложений. У контейнерных плагинов выбраны конкретные версии, переопределяемые через `plugins/.env`. Перед обновлением проверяйте примечания к выбранному образу и сохраняйте резервную копию рабочих данных.

### Zigbee и MQTT

Zigbee2MQTT включается отдельным профилем в `plugins/.env`. Настройка координатора, сохранение данных и запуск описаны в [инструкции плагинов](plugins/README.md#zigbee2mqtt-и-file-editor). Драйвер выбирается под свой координатор, например `zstack`; внутри контейнера путь устройства — `/dev/ttyUSB0`.

Zigbee2MQTT обращается к `mqtt://mosquitto:1883` в общей Docker-сети `ha-backend`; HA — к опубликованному `127.0.0.1:1883` в сети хоста. MQTT discovery нужно включить в Zigbee2MQTT. Базовая тема обычно `zigbee2mqtt`.

После подготовки Mosquitto использует `listener 1883`, анонимный доступ отключён. Файл учётных записей — `/mosquitto/config/passwd` внутри контейнера. Порт `9001` проброшен в Compose, но WebSocket listener в шаблоне не настроен.

### Matter и Bluetooth

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

#### Включение интеграции Matter

Home Assistant и Matter Server работают в режиме `host` на одном Raspberry Pi, поэтому HA может подключаться к серверу через `localhost`.

1. Откройте **Настройки → Устройства и службы** в Home Assistant.
2. Если появилась карточка **«Обнаружено: Matter»**, нажмите **Настроить**.
3. Если карточки нет, нажмите **Добавить интеграцию**, найдите **Matter** и выберите её.
4. Для подключения к уже запущенному серверу укажите **`ws://localhost:5580/ws`**. Если мастер предлагает использовать приложение Matter Server из Home Assistant OS, отключите этот вариант и перейдите к вводу адреса внешнего сервера.
5. Завершите настройку и убедитесь, что интеграция подключилась без ошибок.

Режим `host` позволяет обращаться к серверу по этому адресу; появление карточки автообнаружения зависит от обнаружения сервиса в сети. Порядок подключения внешнего сервера описан в [документации интеграции Matter](https://www.home-assistant.io/integrations/matter/).

#### Ручная загрузка сертификатов при недоступности DCL из РФ

В текущем Compose доступ к `on.dcl.csa-iot.org` намеренно заблокирован. Сертификаты PAA хранятся на хосте в `/home/hauser/homeassistant/matter-data/credentials/`; контейнер видит этот каталог как `/data/credentials` благодаря параметру `--paa-root-cert-dir`.

Остановите Matter Server и подготовьте каталоги на Raspberry Pi:

```bash
cd /home/hauser/homeassistant
docker compose stop matter-server
sudo mkdir -p ./matter-data/credentials
mkdir -p /home/hauser/paa-root-certs
```

Скачайте файлы сертификатов `.der` из каталога [production/paa-root-certs проекта connectedhomeip](https://github.com/project-chip/connectedhomeip/tree/master/credentials/production/paa-root-certs) через **Download raw file**. Если GitHub недоступен с Raspberry Pi, скачайте файлы на другом компьютере и передайте их по SCP/SFTP в `/home/hauser/paa-root-certs/`.

Убедитесь, что загруженные `.der` уже находятся в указанном каталоге. Скопируйте их в хранилище и создайте служебный файл `.version` только после успешного копирования:

```bash
cd /home/hauser/homeassistant
sudo cp /home/hauser/paa-root-certs/*.der ./matter-data/credentials/ &&
  printf '1\n' | sudo tee ./matter-data/credentials/.version > /dev/null
```

В [Matter Server 8.1.0](https://github.com/home-assistant-libs/python-matter-server/blob/8.1.0/matter_server/server/helpers/paa_certificates.py) каталог без `.version` считается устаревшим, и его файлы удаляются перед загрузкой сертификатов. Сохранение этого файла позволяет использовать сертификаты, добавленные вручную. Тег `stable` может указывать на другую версию: перед переносом проверьте её механизм загрузки сертификатов.

После успешного копирования запустите сервер и проверьте его журнал:

```bash
cd /home/hauser/homeassistant
docker compose up -d --no-deps matter-server
docker logs --tail 50 matter-server
```

Ошибки обращения к заблокированному DCL могут оставаться в журнале; проверьте, что сервер загрузил локальные сертификаты и запустился.

#### Перезапуск Matter

```bash
docker restart matter-server
```

Перезапуск сохраняет данные в `matter-data/` и сопряжённые устройства.

#### Логи Matter

Последние 50 строк с просмотром новых сообщений в реальном времени:

```bash
docker logs -f --tail 50 matter-server
```

Для выхода из просмотра нажмите **Ctrl+C**. Контейнер продолжит работать.

Весь доступный журнал контейнера:

```bash
docker logs matter-server
```

#### Пересоздание контейнера Matter с сохранением устройств

```bash
cd /home/hauser/homeassistant
docker compose up -d --no-deps --force-recreate matter-server
```

Compose пересоздаст только Matter Server. Каталог `matter-data/`, сертификаты и данные сопряжения сохранятся.

#### Полный сброс Matter

Команда `sudo rm -rf ./matter-data` удаляет данные Matter-контроллера, включая его ключи, сведения о сопряжённых устройствах и сертификаты. После такого сброса устройства потребуется заново добавить в Home Assistant.

Для полного сброса с возможностью восстановления остановите и удалите контейнер, затем перенесите хранилище в резервную копию:

```bash
cd /home/hauser/homeassistant
docker compose stop matter-server
docker compose rm -f matter-server

backup_dir="backups/matter-reset-$(date +%Y%m%dT%H%M%S%z)"
mkdir -p backups
mkdir -m 700 "$backup_dir"
sudo mv ./matter-data "$backup_dir/matter-data"
```

После успешного переноса создайте новое хранилище и верните в него только сертификаты. Это нужно, поскольку доступ к DCL заблокирован. Если каталога `credentials` в резервной копии нет, загрузите сертификаты по инструкции выше перед запуском сервера. Выполняйте следующие команды в том же терминале, где задана переменная `backup_dir`:

```bash
test -d "$backup_dir/matter-data/credentials" &&
  sudo mkdir -p ./matter-data &&
  sudo cp -a "$backup_dir/matter-data/credentials" ./matter-data/ &&
  printf '1\n' | sudo tee ./matter-data/credentials/.version > /dev/null &&
  docker compose up -d --no-deps matter-server
docker logs --tail 50 matter-server
```

При отсутствии резервного каталога сертификатов цепочка команд не запустит контейнер: сначала загрузите сертификаты по инструкции выше. Прежние данные контроллера сохранятся в `$backup_dir/matter-data/`; новый контейнер создаст отдельную Matter-сеть.

### Прямые интеграции HA

Помимо MQTT, Matter и Bluetooth, можно подключать ESPHome, Tasmota, мобильное приложение HA и другие локальные интеграции. Конкретные устройства, их имена, модели и идентификаторы не публикуются.

В `configuration.yaml` можно использовать `default_config`, `frontend` и подключения файлов `automations.yaml`, `scripts.yaml`, `scenes.yaml`. Реальный каталог `config/`, включая `.storage`, остаётся локальным и исключён из Git.

## Хранение данных

Пути слева указаны относительно `/home/hauser` на Raspberry Pi. Данные каждого контейнерного плагина находятся в его собственной папке внутри `plugins/`.

| Каталог на хосте | Каталог в контейнере | Содержимое |
| --- | --- | --- |
| `homeassistant/config/` | `/config` в HA | YAML, `.storage`, база HA и настройки интеграций |
| `homeassistant/config/` | `/hass-config` в File Editor | Тот же каталог конфигурации; просмотр ограничен этим путём |
| `plugins/hass-configurator/data/` | `/config` в File Editor | Настройки редактора (`settings.conf`, если используется) |
| `plugins/zigbee2mqtt/data/` | `/app/data` | Конфигурация Zigbee2MQTT и данные Zigbee-сети |
| `homeassistant/mosquitto/config/` | `/mosquitto/config` | Настройки брокера и файл учётных записей |
| `homeassistant/mosquitto/data/` | `/mosquitto/data` | Каталог для данных брокера |
| `homeassistant/mosquitto/log/` | `/mosquitto/log` | Каталог для файловых логов брокера |
| `homeassistant/matter-data/` | `/data` | Активная Matter-сеть: `chip.json`, JSON контроллера, файлы SDK, сертификаты `credentials/` и данные OTA `updates/` |
| `backups/`, `homeassistant/backups/` | Не монтируется | Приватные резервные копии Compose и рабочих данных |

HA и Zigbee2MQTT получают часовой пояс хоста через `/etc/localtime`. Учитывайте часовой пояс при сравнении журналов разных сервисов.

### Перенос хранилища Matter

Если предыдущая конфигурация сохраняла активные данные в `/root/.matter_server` внутри контейнера, перед пересозданием перенесите их в каталог `matter-data/` на хосте. В Compose уже заданы пути хранилища, сертификатов и OTA внутри `/data`.

Перед копированием остановите Matter Server и сохраните резервные копии активного хранилища, прежнего `/data` и Compose в локальном `backups/`. Сохраните файлы SDK и сертификаты, затем сверьте контрольные суммы скопированных файлов.

После запуска проверьте загрузку `/data/chip.json` без сброса конфигурации и сохранение ранее сопряжённых устройств. Ключи и идентификаторы Matter-сети не включайте в публичный отчёт. Каталог `matter-data/` исключён из Git и сохраняется при пересоздании контейнера.

## Известные особенности текущей конфигурации

1. **Доступ к DCL намеренно заблокирован в контейнере.** `extra_hosts` направляет `on.dcl.csa-iot.org` на `127.0.0.1`. Поэтому запросы сведений о производителях и новых сертификатов DCL завершаются `Connection refused`. Сохранённые сертификаты явно загружаются из `/data/credentials`.
2. **Один Bluetooth-адаптер используется двумя компонентами.** Интеграция Bluetooth HA и Matter Server обращаются к одному `hci0`; совместное использование нужно учитывать при разборе сбоев BLE.
3. **Интерфейс Matter выбирается отдельно от основного маршрута хоста.** `MATTER_PRIMARY_INTERFACE` должен указывать на существующий интерфейс с доступом к устройствам и рабочим IPv6. При ошибке `Failed to advertise records: Network is unreachable` проверьте выбранный интерфейс, его адреса и маршруты.

Эти особенности относятся к приведённому примеру. Проверяйте интерфейсы и доступность DCL в своей установке.

## Плагины Home Assistant

Контейнерные плагины и управление `COMPOSE_PROFILES` описаны в [plugins/README.md](plugins/README.md). Там находятся команды включения и отключения, миграция из старого Compose, резервное копирование и [инструкция добавления нового контейнерного плагина](plugins/README.md#как-добавить-новый-контейнерный-плагин). Все профили по умолчанию выключены. [Yandex Smart Home](plugins/yandex-smart-home/README.md) устанавливается через HACS внутри HA и не управляется Docker-профилями.

## Инструменты сервера

Конфигурации инструментов хранятся отдельно в [tools/README.md](tools/README.md). Для них используется пример каталога `/home/hauser/tools`: Portainer CE в собственном Compose-проекте и Cockpit как пакет Debian. Compose Uptime Kuma сохранён с закомментированным сервисом. Инструкция описывает копирование шаблонов, создание локальных файлов, настройку адресов панелей, включение мониторинга, обновление и резервное копирование. Пароли и рабочие базы в Git не сохраняются.

## Автозапуск после перезагрузки

Автозапуск обеспечивает Docker: службы `docker.service` и `containerd.service` включены при загрузке, а у включённых контейнеров задано `restart: unless-stopped`. Docker восстанавливает уже созданные контейнеры после перезагрузки. Он не перечитывает `.env` и не создаёт контейнеры после `down`; изменения профилей применяйте скриптом до перезагрузки. См. [правила перезапуска Docker](https://docs.docker.com/engine/containers/start-containers-automatically/).

После подготовки данных, настройки Matter и загрузки образов основного стека и инструментов выполните на сервере:

```bash
bash /home/hauser/homeassistant/system/plugins.sh apply /home/hauser/plugins
bash /home/hauser/homeassistant/system/autostart.sh /home/hauser/homeassistant /home/hauser/tools /home/hauser/plugins
```

[system/autostart.sh](system/autostart.sh) сначала проверяет все Compose и выбор профилей плагинов. Затем при необходимости включает службы Docker через `sudo`, запускает основной стек и инструменты с `--pull never` и проверяет состояние контейнеров. После основного стека вызывает `plugins.sh start`: только выбранные плагины запускаются, отключённые контейнеры проекта `ha-plugins` удаляются с сохранением данных. Подключение к MQTT и параметры приложений настраиваются самими пользователями; в Python нет привязки к отдельным плагинам. Пустая Kuma пропускается. Обновлений и скачивания образов автозапуск не выполняет; первый запуск и обновления плагинов делаются через `plugins.sh apply` при уже работающем основном стеке.

Первые два аргумента остались совместимы с прежней командой, третий задаёт каталог плагинов. Без аргументов в репозитории выбираются соседние `homeassistant/`, `tools/`, `plugins/`; при копировании в `homeassistant/system/` — основной каталог и соседние `tools/`, `plugins/`. Проекты инструментов используют свой `.env` либо общий `tools/.env`; плагины используют только `plugins/.env`. Скрипты `plugins.sh` и `plugins.py` нужно копировать вместе с `autostart.sh`. Python 3.9+ обязателен.

Проверка без изменений:

```bash
bash /home/hauser/homeassistant/system/autostart.sh --check /home/hauser/homeassistant /home/hauser/tools /home/hauser/plugins
systemctl is-enabled docker.service containerd.service
systemctl is-active docker.service containerd.service
docker ps --format 'table {{.Names}}\t{{.Status}}'
```

`--check` не включает службы, не запускает, не останавливает и не удаляет контейнеры. Он сообщает об отсутствующем выбранном или оставшемся отключённом плагине. Состояние `running` без Docker healthcheck не гарантирует исправность всех функций приложения: проверьте журналы и UI отдельно. Cockpit использует `cockpit.socket` и в Docker-стек не входит.

При `unless-stopped` вручную остановленный контейнер остаётся остановленным после перезагрузки. Для восстановления включённых сервисов используйте автозапуск без `--check`. Для полного цикла выполните `sudo reboot`, дождитесь SSH и повторите проверку. При сбое проверьте `journalctl -u docker.service -b`, журналы соответствующего Compose, подключение координатора и адреса панелей.

## Обслуживание

Команды выполняются на Raspberry Pi:

```bash
ssh hauser@192.0.2.10
cd /home/hauser/homeassistant
docker compose ps
docker compose config --quiet
docker compose logs --tail 100 homeassistant matter-server
docker compose logs --tail 100 mosquitto
cd /home/hauser/plugins
docker compose logs --tail 100
bash /home/hauser/homeassistant/system/plugins.sh check /home/hauser/plugins
```

Для резервного копирования Matter нужно сохранять весь каталог `matter-data/`; согласованный снимок следует делать при остановленном Matter Server. Каталог `backups/` содержит приватные данные Matter-сети и доступен владельцу. PIN-коды Matter, MQTT-пароли и содержимое приватных ключей в README не сохраняются.

### Обновление существующей установки

Если Zigbee2MQTT или File Editor ещё работают в старом основном проекте, сначала выполните [миграцию плагинов](plugins/README.md#миграция-из-основного-compose). Далее описано обычное обновление уже разделённых стеков.

С компьютера передавайте только публичные файлы командами из [раздела копирования](#копирование-файлов-на-сервер) и [инструкции инструментов](tools/README.md#копирование-конфигурации-на-сервер). Не копируйте поверх сервера локальные `.env`, `config/`, `matter-data/`, `zigbee2mqtt-data/`, `mosquitto/`, `data/`, `secrets/` и настройки Cockpit. Перед заменой Compose сохраните прежние файлы в приватном `backups/` на сервере.

Проверьте серверные `.env`: Matter и общая сеть — в основном стеке; профили, путь Zigbee, адреса плагинов и пароль редактора — в `plugins/.env`; адрес инструментов — в их `.env`. Compose автоматически читает `.env` из каталога проекта; общий `/home/hauser/tools/.env` для ручных команд указывайте через `--env-file` либо один раз скопируйте в `portainer/.env`, если его ещё нет. Существующий файл не перезаписывайте.

В SSH-сеансе проверьте конфигурации и загрузите образы, пока сервисы ещё работают:

```bash
cd /home/hauser/homeassistant
docker compose config --quiet
docker compose pull
cd /home/hauser/tools/portainer
if [ ! -e .env ] && [ -f ../.env ]; then
  (umask 077; cp ../.env .env)
fi
docker compose config --quiet
docker compose pull
```

Перед применением остановите основной стек и Portainer через `docker compose stop --timeout 60` в соответствующих каталогах. В каталоге плагинов используйте `docker compose --profile '*' stop --timeout 60`, чтобы остановить также оставшиеся контейнеры ранее включённых профилей. Сделайте согласованную резервную копию основного стека, всего каталога `plugins/` и `portainer/data/` вместе с локальными настройками и секретами. Каталоги `backups/` исключите из архива; храните его с правами `600`. Если копирование не удалось, сначала верните контейнеры в работу через `docker compose start`.

Затем примените Compose, пересоздайте контейнеры с сохранением подключённых каталогов и проверьте автозапуск:

```bash
cd /home/hauser/homeassistant
docker compose up -d --pull never --force-recreate
cd /home/hauser/tools/portainer
docker compose up -d --pull never --force-recreate
bash /home/hauser/homeassistant/system/plugins.sh apply /home/hauser/plugins
bash /home/hauser/homeassistant/system/autostart.sh /home/hauser/homeassistant /home/hauser/tools /home/hauser/plugins
```

Проверьте HA (`8123`), Portainer (`9443/api/status`), MQTT (`1883`) и Matter (`5580`). Для включённых плагинов проверьте настроенные адреса Zigbee2MQTT (по умолчанию `8080`) и File Editor (`3218`, без входа ожидается `401`); отключённые плагины проверять не нужно. Сверьте журналы с состоянием до обновления и убедитесь, что прежние интеграции и устройства доступны. После миграции данные контейнерных плагинов сохраняются в `plugins/*/data/`, данные HA — в основном стеке; для отката после миграции формата данных могут понадобиться и прежние образы, и резервная копия. Старые образы не удаляйте до окончания проверки.

## Проверка перед публикацией

Публикуйте только шаблоны, Compose-файлы, скрипты и документацию. `.gitignore` исключает `.env`, `.credentials.env`, пароли Portainer, рабочие каталоги HA/MQTT/Matter, `plugins/*/data/`, настройки редактора, базы, журналы и резервные копии. Файлы `.env.example` и `.credentials.env.example` содержат только пустые или условные значения.

`.gitignore` не исключает файлы, которые уже отслеживаются Git. После подготовки нового репозитория и добавления файлов проверьте состав индекса:

```bash
git status --short --ignored
git diff --cached --name-only
git ls-files
```

В списке для публикации не должно быть файлов с рабочими данными или учётными данными. Также проверьте текст инструкции и скрипты: реальные IP/MAC/IPv6-адреса, имена устройств и идентификаторы сетей не должны попадать в примеры.

При установленном [Gitleaks](https://github.com/gitleaks/gitleaks) проверьте подготовленные изменения без вывода значений секретов:

```bash
gitleaks git --pre-commit --staged --redact --no-banner
```

Проверка относится к текущим подготовленным изменениям, а не к старой истории. Не добавляйте приватные файлы через `git add -f` и не загружайте архив всего рабочего каталога: игнорируемые файлы остаются на диске. Если секрет уже был опубликован, замените его; удаление истории не отзывает пароль или токен.
