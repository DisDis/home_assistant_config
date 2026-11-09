# Yandex Smart Home

[Yandex Smart Home](https://github.com/dext0r/yandex_smart_home) передаёт выбранные устройства Home Assistant в «Дом с Алисой». Это пользовательская интеграция HA; отдельного Docker-контейнера и профиля Compose у неё нет. Установка, обновление и отключение выполняются отдельно от `plugins.sh`. По умолчанию компонент не устанавливается.

Проверьте совместимость установленной версии HA с выбранным релизом компонента. Документация ветки 1.1.x указывает HA 2025.12 или новее; перед установкой и обновлением сверяйте требования [актуальной документации](https://docs.yaha-cloud.ru/v1.1.x/). Ниже описана установка из `repos/` через симлинк, используемая в этом проекте, и альтернативная установка через HACS.

## Установка из repos через симлинк

1. На сервере скачайте репозиторий в `repos/` в корне проекта и подготовьте каталог пользовательских интеграций:

   ```bash
   cd /home/hauser/home_assistant_config
   mkdir -p repos
   sudo mkdir -p homeassistant/config/custom_components
   git clone https://github.com/dext0r/yandex_smart_home.git repos/yandex_smart_home
   ```

   Если клон уже есть, используйте его. Команда скачивает текущую ветку репозитория; для установки конкретного релиза выберите соответствующий совместимый тег в клоне.

2. В [homeassistant/docker-compose.yml](../../homeassistant/docker-compose.yml) у сервиса `homeassistant` уже подключены конфигурация HA и репозитории:

   ```yaml
   volumes:
     - ./config:/config
     - ../repos:/repos
   ```

   Если монтирование `../repos:/repos` добавлено после создания контейнера, примените его пересозданием сервиса:

   ```bash
   cd /home/hauser/home_assistant_config/homeassistant
   docker compose up -d --no-deps homeassistant
   ```

3. Создайте симлинк на каталог компонента:

   ```bash
   cd /home/hauser/home_assistant_config/homeassistant/config/custom_components/
   sudo ln -s /repos/yandex_smart_home/custom_components/yandex_smart_home/ ./yandex_smart_home
   ```

   Цель `/repos/...` — путь **внутри контейнера**, где HA видит скачанный репозиторий. На хосте ссылка может выглядеть нерабочей, если там нет `/repos`. Существующий симлинк повторно создавать не нужно. Если компонент уже установлен через HACS, продолжайте использовать HACS либо сначала удалите компонент через него перед переходом на симлинк.

4. Проверьте доступ к компоненту из контейнера и перезапустите HA:

   ```bash
   cd /home/hauser/home_assistant_config/homeassistant
   docker compose exec -T homeassistant test -f /config/custom_components/yandex_smart_home/manifest.json
   docker compose restart homeassistant
   ```

   Проверка должна завершиться с кодом `0`; при ошибке проверьте клон, монтирование `/repos` и цель ссылки. После перезапуска интеграция доступна в **Настройки → Устройства и службы → Добавить интеграцию → Yandex Smart Home**. Выберите её и завершите мастер.

Установка через симлинк выполняется вручную и не требует HACS. Не используйте HACS для обновления этой же установки; обновляйте её клон в `repos/`.

## Установка через HACS

1. Откройте HACS. Если его нет, следуйте [официальной инструкции для своей версии HA](https://www.hacs.xyz/docs/use/download/download/), выбрав Home Assistant Container. В версиях HA с встроенным Marketplace используйте доступный интерфейс HACS/Marketplace; повторно устанавливать существующий HACS не нужно. Для старых версий инструкция HACS предусматривает загрузку внутри контейнера, перезапуск HA и добавление HACS через интерфейс интеграций.
2. В поиске HACS найдите **Yandex Smart Home**, проверьте репозиторий `dext0r/yandex_smart_home`, скачайте выбранный совместимый релиз.
3. Перезапустите Home Assistant. На сервере, где основной стек находится в `/home/hauser/home_assistant_config/homeassistant`:

   ```bash
   cd /home/hauser/home_assistant_config/homeassistant
   docker compose restart homeassistant
   ```

4. Откройте **Настройки → Устройства и службы → Добавить интеграцию → Yandex Smart Home** и завершите мастер. Установка через HACS [рекомендована автором](https://docs.yaha-cloud.ru/v1.1.x/install/component/).

## Подключение к Яндексу

Выберите подходящий [тип подключения](https://docs.yaha-cloud.ru/v1.1.x/config/connection-type/): облачное Yaha Cloud проще в настройке; прямое требует постоянного доступа к HA из интернета и настройки HTTPS. Настраивайте выбранный вариант по документации компонента. Сам перенос Compose не публикует HA в интернете и не настраивает привязку аккаунта.

Задайте список устройств для передачи Яндексу, свяжите интеграцию в приложении «Дом с Алисой» по шагам мастера и проверьте команду для одного устройства. После этого добавьте остальные нужные устройства. Учётные данные и рабочие настройки сохраняются локально в каталоге HA; их не нужно переносить в Git или `plugins/.env`.

## Обновление, отключение и бэкап

Перед обновлением проверьте совместимость и сохраните резервную копию `homeassistant/config/` с настройками интеграции. Для установки из `repos/` обновите клон, затем перезапустите HA. Если используется текущая ветка:

```bash
cd /home/hauser/home_assistant_config
git -C repos/yandex_smart_home pull --ff-only
cd homeassistant
docker compose restart homeassistant
```

Если установлен конкретный тег, получите новые теги через `git fetch --tags` в клоне и переключитесь на выбранный совместимый релиз вместо `git pull`. При установке через HACS обновляйте компонент через HACS, затем перезапускайте HA.

Для временного отключения используйте меню записи интеграции в **Настройки → Устройства и службы**. Для полного удаления сначала удалите запись интеграции и отвяжите соответствующее подключение в «Доме с Алисой». При ручной установке удалите симлинк командой `sudo unlink /home/hauser/home_assistant_config/homeassistant/config/custom_components/yandex_smart_home`; при установке через HACS удалите компонент через HACS. Затем перезапустите HA. Изменение `COMPOSE_PROFILES` на эту интеграцию не влияет.

В резервную копию входит весь `homeassistant/config/`, включая `.storage` и симлинки в `custom_components`. Сам `repos/` бэкапить не нужно: при восстановлении скачайте расширение заново в `repos/yandex_smart_home/` до запуска HA. Настройки и ссылка восстановятся из архива HA, а монтирование `/repos` сделает код доступным внутри контейнера. При установке через HACS файлы компонента находятся непосредственно в `homeassistant/config/custom_components/yandex_smart_home` и также входят в архив HA. Рабочие `config/` и `repos/` исключены из Git.
