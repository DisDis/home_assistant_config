# Пользовательские интеграции Home Assistant

Каталог `integrations/` в корне проекта предназначен для локальных клонов пользовательских интеграций HA. В Git хранится этот файл; скачанные репозитории и их рабочие файлы игнорируются. Установка интеграций выполняется отдельно от `system/addons.sh` и `COMPOSE_PROFILES`.

```text
integrations/
  readme.md
  yandex_smart_home/       # локальный клон, исключён из Git
```

## Общий порядок установки

Работайте в [клоне проекта на Raspberry Pi](../readme.md#клонирование-репозитория). Пример корня проекта — `/home/hauser/home_assistant_config`; замените его своим путём.

В [Compose Home Assistant](../homeassistant/docker-compose.yml) уже подключены конфигурация и каталог интеграций:

```yaml
volumes:
  - ./config:/config
  - ../integrations:/integrations
```

Для ручной установки скачайте репозиторий в `integrations/ИМЯ_РЕПОЗИТОРИЯ/`, выберите совместимый релиз и создайте симлинк `homeassistant/config/custom_components/ДОМЕН` на `/integrations/ИМЯ_РЕПОЗИТОРИЯ/custom_components/ДОМЕН`. Цель ссылки — путь **внутри контейнера**. На хосте ссылка может выглядеть нерабочей, если там нет `/integrations`.

Если монтирование добавлено после создания контейнера, примените Compose командой `docker compose up -d --no-deps homeassistant` из `homeassistant/`. После установки или обновления кода интеграции перезапустите HA. У тем, карточек и других типов расширений свои пути установки.

Скачивание клона само по себе не добавляет компонент в управление HACS. Выберите один способ установки и обновляйте компонент тем же способом. При переходе с HACS сначала удалите компонент через HACS; существующий каталог компонента не заменяйте симлинком поверх его содержимого.

Для воспроизводимого восстановления сохраняйте полный commit установленного клона в `homeassistant/config/integration-versions/ДОМЕН.commit` после установки и каждого обновления. Этот каталог входит в бэкап HA; команды для Yandex приведены ниже. Локальные изменения кода commit не сохраняет: их нужно отдельно оформить в своём репозитории или сохранить перед обновлением.

## Пример: Yandex Smart Home

[Yandex Smart Home](https://github.com/dext0r/yandex_smart_home) передаёт выбранные устройства Home Assistant в «Дом с Алисой». Компонент работает внутри HA и по умолчанию не устанавливается. Перед установкой и обновлением проверьте совместимость выбранного релиза с вашей версией HA по [документации компонента](https://docs.yaha-cloud.ru/v1.1.x/).

### Ручная установка

1. Скачайте репозиторий и подготовьте каталог компонентов:

   ```bash
   cd /home/hauser/home_assistant_config
   mkdir -p integrations
   sudo mkdir -p homeassistant/config/custom_components
   git clone https://github.com/dext0r/yandex_smart_home.git integrations/yandex_smart_home
   ```

   Если клон уже существует, используйте его. Команда скачивает текущую ветку; для конкретного релиза выберите совместимый тег в клоне до следующих шагов.

2. Создайте симлинк на компонент. Существующий корректный симлинк повторно создавать не нужно:

   ```bash
   cd /home/hauser/home_assistant_config
   sudo ln -s /integrations/yandex_smart_home/custom_components/yandex_smart_home homeassistant/config/custom_components/yandex_smart_home
   ```

3. Сохраните commit установленной версии вместе с конфигурацией HA:

   ```bash
   cd /home/hauser/home_assistant_config
   sudo install -d -m 700 homeassistant/config/integration-versions
   yandex_commit=$(git -C integrations/yandex_smart_home rev-parse --verify HEAD) &&
     printf '%s\n' "$yandex_commit" | sudo tee homeassistant/config/integration-versions/yandex_smart_home.commit > /dev/null
   ```

4. Проверьте доступ из контейнера и перезапустите HA:

   ```bash
   cd /home/hauser/home_assistant_config/homeassistant
   docker compose exec -T homeassistant test -f /config/custom_components/yandex_smart_home/manifest.json &&
     docker compose restart homeassistant
   ```

   Проверка должна завершиться с кодом `0`. При ошибке проверьте клон, монтирование `/integrations` и цель ссылки. После перезапуска откройте **Настройки → Устройства и службы → Добавить интеграцию → Yandex Smart Home** и завершите мастер.

### Установка через HACS

1. Откройте HACS. Если он не установлен, следуйте [инструкции HACS для Home Assistant Container](https://www.hacs.xyz/docs/use/download/download/).
2. Найдите **Yandex Smart Home**, проверьте репозиторий `dext0r/yandex_smart_home` и скачайте совместимый релиз.
3. Перезапустите HA:

   ```bash
   cd /home/hauser/home_assistant_config/homeassistant
   docker compose restart homeassistant
   ```

4. Откройте **Настройки → Устройства и службы → Добавить интеграцию → Yandex Smart Home** и завершите мастер. Установка через HACS [рекомендована автором](https://docs.yaha-cloud.ru/v1.1.x/install/component/).

Файлы компонента при такой установке находятся непосредственно в `homeassistant/config/custom_components/yandex_smart_home/` и входят в бэкап HA. Клон в `integrations/` и запись commit для установки через HACS не нужны.

### Подключение к Яндексу

Выберите [тип подключения](https://docs.yaha-cloud.ru/v1.1.x/config/connection-type/): Yaha Cloud или прямое подключение с постоянным доступом к HA из интернета и HTTPS. Настройте выбранный вариант по документации компонента.

Задайте список устройств для передачи Яндексу, свяжите интеграцию в приложении «Дом с Алисой» по шагам мастера и проверьте команду для одного устройства. Затем добавьте остальные нужные устройства. Учётные данные и рабочие настройки сохраняются локально в конфигурации HA и не публикуются в Git.

### Обновление и отключение

Перед обновлением сохраните резервную копию конфигурации HA с записью текущего commit и проверьте совместимость нового релиза. Для ручной установки из текущей ветки обновите клон, запишите новый commit и перезапустите HA:

```bash
cd /home/hauser/home_assistant_config
git -C integrations/yandex_smart_home pull --ff-only
sudo install -d -m 700 homeassistant/config/integration-versions
yandex_commit=$(git -C integrations/yandex_smart_home rev-parse --verify HEAD) &&
  printf '%s\n' "$yandex_commit" | sudo tee homeassistant/config/integration-versions/yandex_smart_home.commit > /dev/null
cd homeassistant
docker compose restart homeassistant
```

Если установлен конкретный тег, получите теги командой `git fetch --tags` в клоне и переключитесь на выбранный совместимый релиз вместо `git pull`; затем так же сохраните новый commit и перезапустите HA. При установке через HACS обновляйте компонент через HACS и перезапускайте HA.

Для временного отключения используйте меню записи интеграции в **Настройки → Устройства и службы**. Для полного удаления сначала удалите запись интеграции и отвяжите подключение в «Доме с Алисой». При ручной установке удалите симлинк командой `sudo unlink /home/hauser/home_assistant_config/homeassistant/config/custom_components/yandex_smart_home` и сохранённый файл `config/integration-versions/yandex_smart_home.commit`; при установке через HACS удалите компонент через HACS. Затем перезапустите HA.

## Восстановление

Архив HA сохраняет весь `homeassistant/config/`, включая `.storage`, симлинки и `integration-versions/`. Клоны из `integrations/` не входят в бэкап. После распаковки архива HA скачайте нужные репозитории заново и выберите сохранённые commit **до запуска HA**. Для Yandex Smart Home:

```bash
cd /home/hauser/home_assistant_config
mkdir -p integrations
git clone --no-checkout https://github.com/dext0r/yandex_smart_home.git integrations/yandex_smart_home
yandex_commit=$(sudo cat homeassistant/config/integration-versions/yandex_smart_home.commit) &&
  git -C integrations/yandex_smart_home checkout --detach "$yandex_commit"
```

Если клон уже существует, используйте его и при необходимости выполните `git -C integrations/yandex_smart_home fetch --tags` перед переключением commit. Если запись версии отсутствует, выберите совместимый релиз вручную и сохраните его commit по инструкции установки. Восстановленный симлинк должен указывать на `/integrations/yandex_smart_home/custom_components/yandex_smart_home`. После запуска HA проверьте `manifest.json` из контейнера командой из раздела ручной установки.

При установке через HACS код восстанавливается вместе с конфигурацией HA. Отдельного архива Yandex Smart Home нет; настройки интеграции находятся в архиве HA.
