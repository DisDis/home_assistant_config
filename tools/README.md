# Инструменты сервера

Пример отдельных инструментов Raspberry Pi с Debian 13 и ARM64. Адрес `192.0.2.10`, пользователь `hauser`, имя `ha-server` и пути `/home/hauser/...` условные: замените их своими значениями. Основной Compose Home Assistant, контейнерные дополнения и инструменты используют отдельные каталоги. Zigbee2MQTT, File Editor и Matter Server описаны в [addons/README.md](../addons/README.md), пользовательские интеграции HA — в [integrations/readme.md](../integrations/readme.md).

| Инструмент | Состояние шаблона | Адрес после настройки LAN | Способ установки |
| --- | --- | --- | --- |
| Portainer CE 2.45.3 | Включён в Compose | <https://192.0.2.10:9443> | `portainer/docker-compose.yml` |
| Cockpit | Шаблон сокета | <https://192.0.2.10:9090> | Пакеты Debian; [cockpit/listen.conf.example](cockpit/listen.conf.example) |
| Uptime Kuma 2.5.9 | Отключён | <http://192.0.2.10:3001> | Закомментирован в `uptime-kuma/docker-compose.yml` |

По умолчанию Portainer, шаблон Kuma и Cockpit слушают только `127.0.0.1`. Для доступа из LAN создайте на сервере локальный `.env` в каталоге соответствующего Compose-проекта из `tools/.env.example` и задайте `TOOLS_BIND_IP` — реальный LAN IPv4-адрес сервера. Для Cockpit создайте локальную копию `listen.conf.local` и измените в ней `ListenStream=127.0.0.1:9090` до установки. Эта копия исключена из Git. Реальные адреса и локальные настройки не возвращайте в публичные шаблоны.

Для установки нужны Docker с Compose, доступ по SSH, `sudo`, `openssl` и `nano`. Установка Docker и пакетов описана в [основном README](../readme.md#установка-и-настройка-docker). Сначала [склонируйте весь репозиторий на Raspberry Pi](../readme.md#клонирование-репозитория). Все команды ниже выполняются в SSH-сеансе на сервере; каталог инструментов — `/home/hauser/home_assistant_config/tools`.

Portainer и Cockpit используют HTTPS; при самоподписанном сертификате браузер попросит подтвердить его. Uptime Kuma можно включить отдельно по инструкции ниже.

## Что хранить в Git

В репозиторий входят Compose-файлы, скрипт настройки мониторов, примеры переменных, шаблон сокета Cockpit и инструкция. Рабочие каталоги `data/`, `secrets/`, `backups/`, файлы `.env` и `.credentials.env` исключены через корневой `.gitignore`.

Пароль первоначального администратора Portainer хранится только на сервере в `portainer/secrets/admin-password`. Учётные данные Kuma и адрес для мониторинга задаются локально в `.credentials.env`; используйте `.credentials.env.example` как пустой шаблон. Файлы с учётными данными должны иметь права `600`. Рабочие данные хранятся в `portainer/data/` и `uptime-kuma/data/`.

Каждая утилита — независимый Compose-проект. Основной Compose в репозитории находится в [homeassistant/docker-compose.yml](../homeassistant/docker-compose.yml). На сервере в примерах основной стек размещён в `/home/hauser/home_assistant_config/homeassistant`, инструменты — в `/home/hauser/home_assistant_config/tools`.

Автозапуск контейнеров и проверка после перезагрузки описаны в [основном README](../readme.md#автозапуск-после-перезагрузки). Скрипт `system/autostart.sh` запускает включённые проекты инструментов и проверяет их политики перезапуска; отключённая Kuma пропускается. HA и дополнения запускаются через общий `system/ha.sh`, инструменты затем запускаются отдельно; выбор дополнений берётся из `addons/.env`; все профили по умолчанию выключены. Для ручного `docker compose` используйте `.env` в каталоге утилиты либо явно передавайте `--env-file /home/hauser/home_assistant_config/tools/.env`, если настройки хранятся в общем файле.

## Подготовка в клоне репозитория

Все шаблоны уже находятся в `tools/` внутри `home_assistant_config/`: [сокет Cockpit](cockpit/listen.conf.example), [адрес панелей](.env.example), [учётные данные Kuma](.credentials.env.example) и Compose-файлы. Создавайте рабочие `.env`, `listen.conf.local` и файлы с паролями локально по следующим разделам, сохраняя существующие файлы.

До первого запуска контейнера создайте его каталог данных. Для уже настроенной утилиты перенесите прежний `data/` с сохранением прав и владельца до пересоздания контейнера; пустой каталог запустит новую установку. Команды `mkdir -p` ниже сохраняют имеющиеся данные.

Команды `curl` проверяют панель с самого сервера; доступ с другого компьютера требует LAN-привязки и проверки адреса в браузере.

## Установка Portainer CE

Следующие команды выполняются на Raspberry Pi. Пароль создаётся только при отсутствии файла, чтобы повторное выполнение не меняло сохранённые учётные данные.

```bash
cd /home/hauser/home_assistant_config/tools/portainer
mkdir -p data
[ -e .env ] || (umask 077; cp ../.env.example .env)
chmod 600 .env
# Для доступа из LAN задайте TOOLS_BIND_IP в локальном .env.
nano .env
install -d -m 700 secrets
if [ ! -e secrets/admin-password ]; then
  (umask 077; openssl rand -base64 32 > secrets/admin-password)
fi
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
portainer_address="$(docker compose port portainer 9443)"
curl --insecure --fail "https://${portainer_address}/api/status"
```

Панель: <https://192.0.2.10:9443>. Начальный пользователь — `admin`, его пароль находится в `secrets/admin-password`. Аргумент `--admin-password-file` создаёт администратора при первом запуске пустого хранилища. После смены пароля в интерфейсе этот файл не обновляется автоматически и не сбрасывает пароль существующей учётной записи.

При новой установке выберите локальное Docker-окружение через Unix-сокет `/var/run/docker.sock`, если оно ещё не добавлено. Portainer получает административный доступ к Docker этого сервера. Порты `8000` для Edge Agent и `9000` для HTTP не публикуются.

Существующие контейнеры Home Assistant доступны для просмотра состояния, логов и операций с контейнерами. Изменения основного стека делайте через `/home/hauser/home_assistant_config/homeassistant/docker-compose.yml`: установка Portainer не переносит этот файл в его редактор стеков.

Перед обновлением Portainer проверьте совместимость выбранной версии с Docker Engine и свою резервную копию данных.

## Установка Cockpit на сервер

Cockpit устанавливается на Debian как системная служба. Ниже полный набор команд для новой установки; Docker для него не используется.

```bash
# Установить Cockpit, управление дисками и пакетами.
sudo apt-get update
sudo apt-get install --no-install-recommends cockpit cockpit-storaged cockpit-packagekit

# Подготовить локальную настройку; существующий файл сохраняется.
cd /home/hauser/home_assistant_config/tools/cockpit
[ -e listen.conf.local ] || cp listen.conf.example listen.conf.local
# Для LAN замените 127.0.0.1 на свой LAN IPv4-адрес.
nano listen.conf.local
sudo install -d -m 755 /etc/systemd/system/cockpit.socket.d
sudo install -m 644 /home/hauser/home_assistant_config/tools/cockpit/listen.conf.local /etc/systemd/system/cockpit.socket.d/listen.conf

# Остановить уже запущенную веб-службу перед применением настройки сокета.
sudo systemctl stop cockpit.service
sudo systemctl daemon-reload
sudo systemctl enable cockpit.socket
sudo systemctl restart cockpit.socket

# Проверить запуск и доступность.
systemctl is-enabled cockpit.socket
systemctl is-active cockpit.socket
sudo ss -ltnp 'sport = :9090'
cockpit_address="$(awk -F= '/^ListenStream=.+/ {print $2}' listen.conf.local)"
curl --insecure --fail --output /dev/null --write-out '%{http_code}\n' "https://${cockpit_address}/"
```

Откройте <https://192.0.2.10:9090>. Вход — системным пользователем `hauser` и его существующим паролем Linux. У SSH-ключа и пароля Linux разное назначение: вход по SSH-ключу не означает, что этот ключ можно ввести в форму Cockpit. Для административных действий используется режим административного доступа Cockpit и права пользователя в системе.

`cockpit.socket` запускается при загрузке системы и по запросу поднимает веб-службу. Неактивный `cockpit.service` во время простоя допустим, если сокет активен. `FreeBind=true` позволяет сокету запускаться до появления адреса на интерфейсе. Рабочая привязка берётся из локального `listen.conf.local` и устанавливается в `/etc/systemd/system/cockpit.socket.d/listen.conf`.

Сетевой модуль `cockpit-networkmanager` отдельно не устанавливается. Для текущей задачи достаточно служб Linux, журналов, показателей ресурсов, управления дисками и обновлениями пакетов.

Диагностика:

```bash
sudo systemctl status cockpit.socket cockpit.service --no-pager
sudo journalctl -u cockpit.socket -u cockpit.service -n 100 --no-pager
```

[Официальная инструкция Cockpit для Debian](https://cockpit-project.org/running.html#debian).

## Восстановление Uptime Kuma (сейчас отключён)

В шаблоне сервис закомментирован. Чтобы включить Kuma, отредактируйте на сервере `/home/hauser/home_assistant_config/tools/uptime-kuma/docker-compose.yml`: замените `services: {}` на `services:` и снимите один уровень комментария `# ` со строк блока `uptime-kuma`, сохранив YAML-отступы. Затем выполните команды ниже. При существующем `data/` подключится сохранённая база; на новой установке база будет создана при запуске. До включения сервиса команды запуска Kuma пропустите.

```bash
cd /home/hauser/home_assistant_config/tools/uptime-kuma
mkdir -p data
[ -e .env ] || (umask 077; cp ../.env.example .env)
chmod 600 .env
# Для доступа из LAN задайте TOOLS_BIND_IP в локальном .env.
nano .env
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
kuma_address="$(docker compose port uptime-kuma 3001)"
curl --fail --output /dev/null --write-out '%{http_code}\n' "http://${kuma_address}/"
```

Используется SQLite в локальном каталоге `data/`; отдельный сервер базы данных не требуется. При новой установке создайте администратора в веб-интерфейсе либо скриптом ниже.

Для проверок HTTP и TCP Docker-сокет Uptime Kuma не нужен и в контейнер не передаётся. Уведомления настраиваются отдельно в интерфейсе после выбора канала. Мониторинг на самом Raspberry Pi не сможет отправить уведомление при полном отключении этого устройства; для такой проверки нужен внешний наблюдатель.

### Начальные проверки

После включения и запуска Kuma скрипт `setup-monitors.js` создаёт администратора только в пустой установке, затем добавляет отсутствующие по имени проверки. Существующие учётные записи и проверки не изменяются. Скрипт запускается вручную на сервере:

```bash
cd /home/hauser/home_assistant_config/tools
[ -e .credentials.env ] || (umask 077; cp .credentials.env.example .credentials.env)
chmod 600 .credentials.env
# Задайте MONITOR_HOST, UPTIME_KUMA_USERNAME и UPTIME_KUMA_PASSWORD.
nano .credentials.env
set -a
. ./.credentials.env
set +a
docker exec -i -e MONITOR_HOST -e UPTIME_KUMA_USERNAME -e UPTIME_KUMA_PASSWORD uptime-kuma node - < uptime-kuma/setup-monitors.js
unset MONITOR_HOST PORTAINER_USERNAME PORTAINER_PASSWORD UPTIME_KUMA_USERNAME UPTIME_KUMA_PASSWORD
```

В `MONITOR_HOST` укажите реальный IPv4-адрес или DNS-имя сервера, доступное из контейнера Kuma. Не используйте `127.0.0.1`: внутри контейнера он указывает на сам контейнер. Для проверок Portainer и Cockpit настройте их LAN-привязку; при привязке только к `127.0.0.1` контейнер Kuma не сможет обратиться к ним по LAN-адресу. `UPTIME_KUMA_USERNAME` и `UPTIME_KUMA_PASSWORD` должны совпадать с учётной записью Kuma; на пустой базе скрипт создаст её. Храните файл с правами `600`.

Шаблон содержит семь проверок раз в минуту, с двумя повторными попытками перед подтверждением сбоя. Проверка отключённого Cloud Commander закомментирована в скрипте. При восстановлении существующей базы приостановите или удалите старую проверку Cloud Commander, если этот сервис остаётся отключённым.

| Сервис | Проверка |
| --- | --- |
| Home Assistant | HTTP `8123`, успешный ответ |
| Mosquitto | Соединение TCP `1883` |
| Zigbee2MQTT | HTTP `8080`, успешный ответ |
| Matter Server | Соединение TCP `5580` |
| HASS Configurator | HTTP `3218`, ожидается запрос авторизации `401` |
| Portainer | HTTPS `9443/api/status`, успешный ответ |
| Cockpit | HTTPS `9090`, успешный ответ |

Создавайте проверки Zigbee2MQTT, File Editor и Matter Server только для включённых профилей и используйте настроенные адреса и порты. При loopback и стандартных портах доступны адреса хоста через SSH-туннель; контейнер Kuma должен иметь отдельный доступ к хосту для таких проверок. Для File Editor пароль в монитор не передаётся: ответ `401` подтверждает доступность страницы с защитой. Эта проверка не проверяет работу функций редактора после входа. Для Portainer и Cockpit в проверках отключена проверка доверия к самоподписанному сертификату. Проверки портов подтверждают доступность TCP-соединения, а не исправность всех функций MQTT или Matter.

## Обновление и резервное копирование

В Compose инструментов используются теги версий. Для обновления измените тег, получите изменения в серверном клоне через `git pull --ff-only` по [основной инструкции](../readme.md#обновление-существующей-установки) и выполните команды в каталоге соответствующего **включённого** проекта: `/home/hauser/home_assistant_config/tools/portainer` либо `/home/hauser/home_assistant_config/tools/uptime-kuma`. При отключённой Kuma её обновление пропустите.

```bash
docker compose config --quiet
docker compose pull
docker compose up -d
docker compose ps
docker compose logs --tail 100
```

Для Cockpit используются обычные обновления пакетов Debian:

```bash
sudo apt-get update
sudo apt-get install --only-upgrade cockpit cockpit-system cockpit-bridge cockpit-ws cockpit-storaged cockpit-packagekit
```

Для восстановления нужны Compose-файлы, локальные `.env`, `portainer/data/`, `portainer/secrets/`, `uptime-kuma/data/` и защищённая копия учётных данных. Для согласованной файловой копии базы остановите только соответствующую утилиту через `docker compose stop`, скопируйте её данные и запустите `docker compose start`. Резервную копию храните также на другом устройстве. Для Cockpit сохраните `cockpit/listen.conf.local` и дополнительные настройки из `/etc/cockpit`, если они появятся.

## Проверка после установки

Проверьте доступность панелей с нужных устройств и работу входа. Portainer и Cockpit должны использовать HTTPS. Запрос защищённого API без авторизации должен быть отклонён. После перезапуска контейнеров должны сохраняться учётные записи, Docker-окружение и мониторы Kuma.

В Portainer подключите локальное Docker-окружение через Unix-сокет. В Kuma проверьте состояние мониторов и отдельно настройте уведомления. Для Cockpit проверьте активность сокета и вход системным пользователем.

Отчёты проверки, журналы и снимки состояния сохраняйте локально в `backups/`. Не публикуйте выгрузки API, базы панелей, реальные сетевые адреса, файлы с учётными данными и содержимое секретов. Перед публикацией выполните [проверку основного репозитория](../readme.md#проверка-перед-публикацией).
