# OpenThread Border Router

Сервис и профиль `otbr` находятся в [общем Compose дополнений](../docker-compose.yml). По умолчанию профиль выключен. Используется официальный образ `openthread/border-router:sha-783547e`; тег сборки и архитектуры `linux/arm64`, `linux/arm/v7`, `linux/amd64` опубликованы на [Docker Hub OpenThread](https://hub.docker.com/r/openthread/border-router/tags). Образ можно заменить через `OTBR_IMAGE` в `addons/.env`.

Пример настроен на **SONOFF Dongle Plus MG24 (Dongle-PMG24) с уже установленной OpenThread RCP 2.4.4**: UART **460800**, аппаратный flow control **выключен**. Прошивать адаптер повторно не требуется. Параметр `uart-flow-control` в URL отсутствует; не добавляйте его даже в виде `uart-flow-control=0`. Инструкции производителя: [OpenThread на Dongle-PMG24](https://dongle.sonoff.tech/guide/dongle-pmg24/how_to_flash_openthread_firmware/) и [SONOFF Dongle Flasher](https://dongle.sonoff.tech/sonoff-dongle-flasher/).

OTBR маршрутизирует IPv6 между Thread и локальной сетью. Для Matter over Thread нужен также [Matter Server](../matter-server/README.md); это отдельный профиль. Адаптер с прошивкой Thread RCP используется только OTBR: Zigbee2MQTT и ZHA должны работать с другим координатором. Закройте flasher и другие программы, обращающиеся к этому последовательному порту.

## Подготовка Raspberry Pi

Работайте в [серверном клоне проекта](../../readme.md#клонирование-репозитория). Найдите постоянные пути адаптеров:

```bash
ls -l /dev/serial/by-id/
```

В существующем `addons/.env` добавьте параметры из [`.env.example`](../.env.example), сохранив остальные настройки:

```dotenv
OTBR_IMAGE=openthread/border-router:sha-783547e
OTBR_ADAPTER_PATH=/dev/serial/by-id/...
OTBR_RCP_DEVICE=spinel+hdlc+uart:///dev/ttyOTBR?uart-baudrate=460800
OTBR_INFRA_IF=wlan0
OTBR_THREAD_IF=wpan0
OTBR_LOG_LEVEL=6
OTBR_REST_PORT=8081
OTBR_WEB_PORT=8082
```

Замените `...` полным ID SONOFF из вывода команды. `OTBR_ADAPTER_PATH` — путь на хосте; внутри контейнера адаптер всегда доступен как `/dev/ttyOTBR`. Для `ZIGBEE_ADAPTER_PATH` выберите путь отдельного Zigbee-координатора. `OTBR_INFRA_IF` должен указывать на интерфейс локальной сети с рабочим IPv6, например `wlan0` или `eth0`; обычно он совпадает с `MATTER_PRIMARY_INTERFACE`. Для другой прошивки скорость UART выбирайте по её параметрам.

На хосте должны быть включены IPv6, IP forwarding и приём Router Advertisement при маршрутизации. Следующие команды выполняются **один раз на Raspberry Pi**, перед запуском OTBR; значение `otbr_infra_if` должно совпадать с `OTBR_INFRA_IF`. Они сохраняют сетевые параметры в `/etc/sysctl.d/60-otbr.conf` и сразу применяют их:

```bash
otbr_infra_if=wlan0
ip link show "$otbr_infra_if"
sysctl net.ipv6.conf.all.disable_ipv6 "net.ipv6.conf.${otbr_infra_if}.disable_ipv6"
sudo modprobe tun
test -c /dev/net/tun

sudo tee /etc/sysctl.d/60-otbr.conf > /dev/null <<EOF
net.ipv6.conf.all.forwarding = 1
net.ipv4.ip_forward = 1
net.ipv6.conf.${otbr_infra_if}.accept_ra = 2
net.ipv6.conf.${otbr_infra_if}.accept_ra_rt_info_max_plen = 64
EOF
sudo sysctl -p /etc/sysctl.d/60-otbr.conf
```

Параметры `disable_ipv6` должны быть `0`; если IPv6 отключён, включите его в сетевой конфигурации хоста до продолжения. Проверяйте ошибки каждой команды. Эти параметры соответствуют [официальной подготовке хоста OpenThread](https://openthread.io/guides/border-router/build-docker#enable-ip-forwarding). При изменении LAN-интерфейса обновите также sysctl. В режиме `host` сетевые sysctl задаются на хосте, а не в Compose.

## Включение

Каталог `addons/otbr/data/` создаётся при клонировании благодаря `.stub`. На существующей установке `mkdir -p` сохранит данные:

```bash
cd /home/hauser/home_assistant_config
mkdir -p addons/otbr/data
nano addons/.env
```

Добавьте `otbr` к текущей строке `COMPOSE_PROFILES`, сохранив нужные профили. Например, для Matter over Thread: `COMPOSE_PROFILES=matter-server,otbr`. Перед запуском Matter подготовьте сертификаты по его инструкции. Затем, после запуска основного стека, выполните:

```bash
bash /home/hauser/home_assistant_config/system/addons.sh validate /home/hauser/home_assistant_config/addons
bash /home/hauser/home_assistant_config/system/addons.sh apply /home/hauser/home_assistant_config/addons
bash /home/hauser/home_assistant_config/system/addons.sh check /home/hauser/home_assistant_config/addons
docker logs --tail 100 otbr
curl --fail --max-time 10 http://127.0.0.1:8081/node/state
```

`validate` проверяет только Compose. Состояние `disabled` до настройки Thread-сети допустимо. После подключения к HA и образования сети ожидается `leader`, `router` или `child`; `detached` означает, что маршрутизатор ещё не присоединился. Ошибки Spinel/таймауты RCP требуют проверки выбранного устройства, скорости `460800`, отсутствия `uart-flow-control` и других программ, занявших адаптер.

## Подключение к Home Assistant

Если Thread-сеть уже существует, сначала импортируйте её учётные данные в HA и выберите её предпочтительной в интеграции Thread, затем подключайте пустой OTBR. При первичной настройке HA применяет предпочтительный dataset или создаёт новый; сохранённую сеть не сбрасывайте.

1. В HA откройте **Настройки → Устройства и службы → Добавить интеграцию → OpenThread Border Router**.
2. Укажите REST API **`http://127.0.0.1:8081`**. Home Assistant и OTBR работают в сети хоста одного Raspberry Pi. При изменении `OTBR_REST_PORT` измените адрес интеграции и команды проверки.
3. Настройте интеграцию **Thread**, выберите предпочтительную сеть и синхронизируйте её учётные данные с телефоном через Home Assistant Companion.
4. Для Matter over Thread подключите [интеграцию Matter](../matter-server/README.md#включение-интеграции-matter) и добавляйте устройства через Companion.

Порядок настройки REST API описан в [документации интеграции OTBR](https://www.home-assistant.io/integrations/otbr/), управление сетью и передача учётных данных телефону — в [документации Thread](https://www.home-assistant.io/integrations/thread/). Инициализация dataset при подключении реализована в [интеграции OTBR Home Assistant](https://github.com/home-assistant/core/blob/dev/homeassistant/components/otbr/config_flow.py).

Контейнер использует `network_mode: host`, capability `NET_ADMIN`, адаптер и `/dev/net/tun`; полный `privileged` не нужен. OTBR создаёт интерфейс `wpan0`, маршруты и правила firewall на хосте. Для Thread необходимы IPv6 и multicast в LAN; изоляция клиентов Wi-Fi/VLAN и блокировка mDNS могут мешать обнаружению и сопряжению.

REST API слушает только `127.0.0.1:8081`, веб-интерфейс — `127.0.0.1:8082`. `ADDONS_BIND_IP` на них не влияет. Порт веб-интерфейса выбран отдельно от Zigbee2MQTT (`8080`). Для доступа с компьютера используйте SSH-туннель:

```bash
ssh -L 8082:127.0.0.1:8082 hauser@192.0.2.10
```

Откройте `http://127.0.0.1:8082` на компьютере. Для подключения HA достаточно REST API; изменение сети в веб-интерфейсе также меняет рабочую Thread-сеть.

## Данные и обслуживание

Весь `/data` сохраняется в `addons/otbr/data/`, включая подкаталог `thread/` с dataset и ключами Thread-сети. Общий [скрипт бэкапа](../README.md#резервное-копирование) автоматически включает его в `addon-otbr-МЕТКА.tar.gz`, а Compose и `.env` — в `addons-config-МЕТКА.tar.gz`. Сохраняйте данные при переносе и восстановлении; удаление dataset потребует восстановления сети или повторного сопряжения устройств. Сетевые параметры `/etc/sysctl.d/60-otbr.conf` не входят в бэкап проекта: на новом хосте повторите подготовку выше.

Для просмотра журнала и перезапуска:

```bash
docker logs -f --tail 100 otbr
# Ctrl+C завершает только просмотр журнала.
docker restart otbr
```

Для обновления сделайте бэкап, укажите совместимый тег или digest в `OTBR_IMAGE` и выполните [обычные команды обновления дополнений](../README.md#обновление-версий). Для отключения удалите `otbr` из `COMPOSE_PROFILES` и выполните `addons.sh apply`: каталог данных сохранится. Системные sysctl, заданные при подготовке хоста, при отключении профиля автоматически не отменяются.
