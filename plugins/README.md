# Плагины Home Assistant

Контейнерные плагины работают в независимом Compose-проекте `ha-plugins`. Основной стек находится в `../homeassistant`, инструменты администрирования — в `../tools`. Все контейнерные плагины по умолчанию отключены. Yandex Smart Home устанавливается отдельно через [HACS](yandex-smart-home/README.md).

```text
plugins/
  docker-compose.yml
  .env
  zigbee2mqtt/
    README.md
    data/                 # создаётся при запуске
  hass-configurator/
    README.md
    data/
  matter-server/
    README.md
    data/
  yandex-smart-home/
    README.md             # компонент HACS хранится в конфигурации HA
```

Команды ниже выполняются на сервере. Пример размещения: основной стек `/home/hauser/homeassistant`, плагины `/home/hauser/plugins`, скрипты `/home/hauser/homeassistant/system`. В клоне репозитория `system/` находится в корне: замените путь к скрипту. Для скриптов нужны Bash, Python 3.9+ и Docker Compose v2.27+; Python использует только стандартную библиотеку.

## Копирование и подготовка

На компьютере из корня репозитория передайте публичные файлы:

```bash
ssh hauser@192.0.2.10 'mkdir -p /home/hauser/plugins/yandex-smart-home /home/hauser/plugins/zigbee2mqtt /home/hauser/plugins/hass-configurator /home/hauser/plugins/matter-server /home/hauser/homeassistant/system'
scp plugins/docker-compose.yml plugins/.env.example plugins/README.md hauser@192.0.2.10:/home/hauser/plugins/
scp plugins/yandex-smart-home/README.md hauser@192.0.2.10:/home/hauser/plugins/yandex-smart-home/
scp plugins/zigbee2mqtt/README.md hauser@192.0.2.10:/home/hauser/plugins/zigbee2mqtt/
scp plugins/hass-configurator/README.md hauser@192.0.2.10:/home/hauser/plugins/hass-configurator/
scp plugins/matter-server/README.md hauser@192.0.2.10:/home/hauser/plugins/matter-server/
scp system/plugins.sh system/plugins.py system/autostart.sh hauser@192.0.2.10:/home/hauser/homeassistant/system/
```

На сервере создайте локальный `.env`, сохраняя уже существующий:

```bash
cd /home/hauser/plugins
[ -e .env ] || (umask 077; cp .env.example .env)
chmod 600 .env
nano .env
bash /home/hauser/homeassistant/system/plugins.sh validate /home/hauser/plugins
```

## Выбор плагинов

В `.env` задаётся одна строка `COMPOSE_PROFILES`:

| Строка | Включённые контейнеры |
| --- | --- |
| `COMPOSE_PROFILES=` | Ни одного |
| `COMPOSE_PROFILES=zigbee2mqtt` | Zigbee2MQTT |
| `COMPOSE_PROFILES=hass-configurator` | File Editor |
| `COMPOSE_PROFILES=matter-server` | Matter Server |
| `COMPOSE_PROFILES=zigbee2mqtt,hass-configurator` | Zigbee2MQTT и File Editor |
| `COMPOSE_PROFILES=zigbee2mqtt,matter-server` | Zigbee2MQTT и Matter Server |
| `COMPOSE_PROFILES=hass-configurator,matter-server` | File Editor и Matter Server |
| `COMPOSE_PROFILES=zigbee2mqtt,hass-configurator,matter-server` | Все три |

Профили — штатный механизм [Docker Compose](https://docs.docker.com/compose/how-tos/profiles/). Переменные окружения терминала имеют приоритет над `.env`; удалите старый экспорт `COMPOSE_PROFILES` через `unset COMPOSE_PROFILES`, если хотите использовать файл. Скрипт отклоняет неизвестные профили и `*`, чтобы опечатка не оставляла неожиданное состояние.

| Переменная | Назначение |
| --- | --- |
| `HA_DIR` | Основной стек для общего `config/` File Editor; по умолчанию `../homeassistant`, относительно каталога Compose плагинов |
| `HA_BACKEND_NETWORK` | Имя общей сети; одинаковое в основном стеке и плагинах, по умолчанию `ha-backend` |
| `PLUGINS_BIND_IP` | Адрес веб-интерфейсов; `127.0.0.1` по умолчанию, для LAN укажите LAN IPv4 сервера |
| `ZIGBEE2MQTT_PORT`, `HASS_CONFIGURATOR_PORT` | Порты хоста, по умолчанию `8080` и `3218` |
| `ZIGBEE2MQTT_IMAGE`, `HASS_CONFIGURATOR_IMAGE`, `MATTER_SERVER_IMAGE` | Образы с выбранными версиями или digest |
| `ZIGBEE_ADAPTER_PATH` | Путь к координатору на хосте, желательно `/dev/serial/by-id/...`; нужен только Zigbee2MQTT |
| `HC_USERNAME`, `HC_PASSWORD` | Логин и непустой пароль; нужны только включённому File Editor |
| `MATTER_PRIMARY_INTERFACE`, `MATTER_BLUETOOTH_ADAPTER` | Интерфейс с IPv6 и индекс BlueZ для Matter; по умолчанию `wlan0`, `0` |

Пароль заключайте в одинарные кавычки, чтобы `$` оставался буквальным символом. Рабочие `.env` и данные не публикуются. Скрипт не исполняет `.env` как shell-код и не выводит пароль. Для выключенного File Editor пароль не требуется; для выключенного Zigbee2MQTT координатор не требуется. Скрипт не знает названий плагинов, не проверяет пароли, координаторы или настройки приложений: пользователь заполняет их по инструкции плагина, а ошибки обнаруживают Docker и само приложение при запуске.

## Применение и отключение

Сначала подготовьте и запустите основной стек по [основной инструкции](../readme.md#первый-запуск-сервисов), затем выберите профили и выполните:

```bash
bash /home/hauser/homeassistant/system/plugins.sh validate /home/hauser/plugins
bash /home/hauser/homeassistant/system/plugins.sh apply /home/hauser/plugins
bash /home/hauser/homeassistant/system/plugins.sh check /home/hauser/plugins
```

Команды скрипта:

| Команда | Действие |
| --- | --- |
| `validate` | Проверить разбор Compose и выбор профилей без Docker daemon и изменений; параметры приложений не проверяются |
| `apply` | Проверить принадлежность контейнеров проекту, скачать образы выбранных сервисов, удалить отключённые контейнеры и запустить выбранные |
| `start` | Применить тот же выбор без скачивания образов; используется автозапуском |
| `check` | Проверить состав контейнеров, состояние, политику перезапуска и Docker healthcheck, если он задан, без изменений |

Чтобы отключить плагин, удалите его имя из `COMPOSE_PROFILES` и снова выполните `apply`. Для отключения всех оставьте пустое значение. Скрипт останавливает и удаляет контейнеры своего Compose-проекта по меткам Docker, включая сервисы, удалённые из Compose; одноразовые `docker compose run` пропускаются. Каталоги данных, тома и образы сохраняются. При пустом выборе скачивание и запуск пропускаются, включая отсутствие требования к общей сети и MQTT. Повторный `apply` безопасно применяет тот же выбор. Порядок остановки, запуска и перезапуска HA вместе со всеми плагинами приведён в [основной инструкции](../readme.md#управление-всей-системой).

Само редактирование `.env` не останавливает уже созданные контейнеры, в том числе после перезагрузки. Применяйте изменения скриптом до перезагрузки. Прямое указание сервиса в `docker compose up zigbee2mqtt` обходит выключенный профиль; используйте скрипт как точку запуска и обновления. File Editor дополнительно проверяет непустые учётные данные при старте самого контейнера.

При ошибке разбора Compose, чужом владельце контейнера или сбое загрузки образа скрипт завершается до удаления отключённых контейнеров. Параметры приложений проверяются при их запуске, а запуск нескольких сервисов не является транзакцией: при частичном сбое исправьте настройки и повторите `apply`. Compose ожидает запуска или Docker healthcheck до 60 секунд. Без healthcheck состояние `running` не подтверждает работу всех функций приложения.

## Zigbee2MQTT и File Editor

Mosquitto создаёт сеть `ha-backend` в основном стеке. Zigbee2MQTT подключается к ней как к внешней сети и использует `mqtt://mosquitto:1883`; `depends_on` между независимыми проектами не применяется. Основной стек запускается первым и создаёт сеть. Параметры подключения к MQTT задаются в самом приложении; подключение и повторные попытки выполняет Zigbee2MQTT. Специальной проверки MQTT в скрипте нет. HA продолжает подключаться к брокеру через `127.0.0.1:1883` в сети хоста. File Editor не требует общей MQTT-сети.

Для Zigbee2MQTT настройте `ZIGBEE_ADAPTER_PATH`, включите профиль и выполните `apply`. В контейнере координатор доступен как `/dev/ttyUSB0`. На новой установке откройте настроенный порт `8080`, пройдите [onboarding](https://www.zigbee2mqtt.io/guide/getting-started/#onboarding), задайте MQTT-адрес, учётные данные брокера и драйвер координатора, например `zstack`. Включите frontend на внутреннем порту `8080` и интеграцию Home Assistant. На действующей установке сохраните конфигурацию сети. Проброшенного устройства достаточно для доступа к координатору; полный `privileged` плагину не выдаётся.

Каждый контейнерный плагин имеет свой каталог: [zigbee2mqtt/](zigbee2mqtt/README.md) и [hass-configurator/](hass-configurator/README.md). Данные Zigbee хранятся в `plugins/zigbee2mqtt/data/`, собственные настройки редактора — в `plugins/hass-configurator/data/`. Они монтируются в `/app/data` и `/config` соответственно. File Editor дополнительно открывает существующий `${HA_DIR}/config` в `/hass-config`; Docker откажет в запуске при отсутствии этого каталога и не создаст пустую копию. Сохраняйте весь `data/` при резервном копировании и не создавайте новую Zigbee-сеть поверх существующей конфигурации.

По умолчанию веб-интерфейсы слушают loopback. Для временного доступа используйте SSH-туннель, выполнив на компьютере:

```bash
ssh -L 8080:127.0.0.1:8080 -L 3218:127.0.0.1:3218 hauser@192.0.2.10
```

После этого доступны `http://127.0.0.1:8080` и `http://127.0.0.1:3218`. Для постоянного доступа из LAN задайте `PLUGINS_BIND_IP`. При изменении портов хоста измените туннель и мониторы. File Editor использует HTTP с авторизацией.

## Matter Server

[Инструкция Matter Server](matter-server/README.md) описывает включение профиля `matter-server`, подготовку сертификатов, сетевой интерфейс, Bluetooth, подключение интеграции в HA и обслуживание. Данные контроллера хранятся в `plugins/matter-server/data/` и входят в общий бэкап `plugins/`.

По умолчанию профиль выключен. Сервер использует сеть хоста и порт `5580`; `PLUGINS_BIND_IP` и переназначение портов веб-интерфейсов на него не действуют. Параметры Matter задаются в `plugins/.env`; основной Compose их не использует.

## Обновление и резервное копирование

Шаблон фиксирует Zigbee2MQTT `2.14.3` и File Editor `0.6.0`; существование тегов проверяется по [релизам Zigbee2MQTT](https://github.com/Koenkk/zigbee2mqtt/releases) и [Docker Hub File Editor](https://hub.docker.com/r/causticlab/hass-configurator-docker/tags). Для Matter сохранён прежний образ с тегом `stable`, переопределяемый через `MATTER_SERVER_IMAGE`; этот тег меняется со временем. Версии и digest выбираются в локальном `.env` после проверки совместимости, примечаний к релизу и резервного копирования. `plugins.sh apply` скачивает только выбранные плагины; не используйте `--profile '*' pull` в обслуживании. Ротация Docker-логов настроена на три файла по 10 МБ для каждого плагина.

Для бэкапа контейнерных плагинов достаточно сохранять весь `plugins/`: Compose, `.env`, инструкции и все `*/data/`. Остановите работающие контейнеры на время согласованной файловой копии. `--profile '*'` в команде **stop** охватывает все существующие профили и ничего не запускает и не скачивает:

```bash
cd /home/hauser/plugins
docker compose --profile '*' stop --timeout 60
cd /home/hauser
umask 077
mkdir -p backups
plugins_backup="backups/plugins-$(date +%Y%m%d-%H%M%S).tar.gz"
sudo tar -czf - plugins > "$plugins_backup"
bash /home/hauser/homeassistant/system/plugins.sh start /home/hauser/plugins
```

Если архивирование завершилось ошибкой, сохраните сообщение ошибки, верните контейнеры через `start` и повторите бэкап после исправления причины. Архив содержит секреты из `.env` и данных приложений; храните его с правами `600` и копией на другом устройстве. При восстановлении распакуйте весь каталог в прежнее место с сохранением владельцев и прав, проверьте `.env` и выполните `plugins.sh apply`.

Конфигурация HA, которую редактирует File Editor, и компоненты HACS, включая Yandex Smart Home, находятся в `homeassistant/config/`. Они входят в отдельный бэкап Home Assistant. Архив `plugins/` не заменяет бэкап HA. Образы не удаляются при отключении; очистку выполняйте отдельно после проверки обновления. Все каталоги `plugins/*/data/`, рабочие `.env` и бэкапы исключены из Git.

## Как добавить новый контейнерный плагин

Новый плагин добавляется через Compose, `.env` и собственную папку. **Менять `system/plugins.py` не требуется:** скрипт получает все сервисы и профили из Compose и управляет их контейнерами по меткам проекта. Он не содержит списка плагинов и не проверяет настройки приложений.

Контейнер автоматически не появляется в списке интеграций или боковой панели HA. Подключение к Home Assistant настройте по документации приложения: в Home Assistant Container нет Supervisor и магазина его дополнений.

1. Найдите официальный образ с поддержкой архитектуры сервера, выберите конкретный тег или digest. Уточните внутренние порты, каталоги данных, авторизацию и протокол подключения к HA.

2. Создайте `plugins/my-plugin/README.md`. Все рабочие файлы приложения храните в `plugins/my-plugin/data/`; этот путь уже исключён из Git. Если нужны несколько монтирований, используйте подкаталоги `data/config`, `data/database` и т. п.

3. Добавьте сервис в `plugins/docker-compose.yml`. Ниже условный шаблон: замените образ, порты и `/data` параметрами своего приложения. Якорь логирования уже есть в общем файле.

   ```yaml
   my-plugin:
     profiles: [my-plugin]
     image: ${MY_PLUGIN_IMAGE:-registry.example.org/vendor/my-plugin:1.2.3}
     restart: unless-stopped
     ports:
       - "${PLUGINS_BIND_IP:-127.0.0.1}:${MY_PLUGIN_PORT:-8090}:8080"
     volumes:
       - ./my-plugin/data:/data
     logging: *plugin-logging
   ```

   Обязательно задайте `profiles`, иначе сервис станет включённым по умолчанию. `container_name` необязателен: Compose может сформировать имя. Для нескольких контейнеров одного плагина назначьте им один профиль и настройте `depends_on`; скрипт автоматически обработает все выбранные сервисы. Без необходимости не выдавайте полный доступ к хосту или Docker-сокету.

4. Добавьте в `.env.example` переменные `MY_PLUGIN_IMAGE`, `MY_PLUGIN_PORT` и пустые секреты, **сохранив `COMPOSE_PROFILES=`**. Пользователь сам заполняет параметры приложения в своём `.env` или `my-plugin/data/`. Для необязательного сервиса не используйте `${PASSWORD:?...}` в общем Compose: интерполяция обрабатывает и отключённые сервисы. Если приложение допускает запуск без нужного секрета, используйте его штатный способ запрета такого запуска либо проверку в команде контейнера, как у File Editor; проверка не должна зависеть от Python-скрипта.

5. Если нужен MQTT, добавьте сервису `networks: [ha-backend]` и настройте в приложении `mqtt://mosquitto:1883` с учётными данными брокера. Сначала запускайте основной стек. `depends_on` не связывает независимые проекты; поведение при недоступном брокере и повторные подключения задаются самим приложением. Для остальных плагинов обычно достаточно сети проекта по умолчанию. HA с `network_mode: host` обращается к опубликованным портам на `127.0.0.1`, а не к DNS-именам сервисов Docker.

6. Опишите в `my-plugin/README.md` нужные настройки, подключение к HA, содержимое `data/`, порядок обновления и бэкапа. При необходимости задайте Docker `healthcheck` прямо в сервисе: `apply` ожидает его, а `check` проверяет состояние.

7. Передайте на сервер обновлённые Compose и инструкцию. Дополните существующий `plugins/.env` новыми параметрами, добавьте `my-plugin` в `COMPOSE_PROFILES`, выполните `plugins.sh validate`, `apply`, `check`. `validate` проверяет только Compose и профили; работу приложения и связь с HA проверьте отдельно.

8. Удалите профиль из `.env`, снова выполните `apply` и убедитесь, что контейнеры удалены, а `my-plugin/data/` сохранён. Повторное включение должно восстановить настройки. Проверяйте только изменённый сервис и затронутые команды; перебор сочетаний профилей не требуется. Общие локальные проверки: `python3 -m unittest discover -s tests -v`; они используют настоящий Compose и имитацию Docker daemon без скачивания образов. Тест с произвольным новым плагином подтверждает, что правки Python не нужны.
