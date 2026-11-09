# HASS Configurator / File Editor

Работайте в [клоне проекта на Raspberry Pi](../../readme.md#клонирование-репозитория). До запуска подготовьте `plugins/hass-configurator/data/`: в Git для него есть пустой `.stub`; рабочие файлы внутри игнорируются.

Сервис и профиль `hass-configurator` определены в [общем Compose](../docker-compose.yml); по умолчанию профиль выключен. В `plugins/.env` задайте `HC_USERNAME` и непустой `HC_PASSWORD`, включите профиль и выполните [общую инструкцию](../README.md#применение-и-отключение).

Собственные настройки редактора, включая `settings.conf`, находятся в `plugins/hass-configurator/data/` и монтируются в `/config`. Редактор открывает `${HA_DIR}/config` в `/hass-config`: это общий каталог работающего HA, а не копия для плагина. Для полного бэкапа сохраняйте и `data/` редактора, и `homeassistant/config/`.

Для штатной остановки используется `stop_signal: SIGINT`: Configurator 0.6.0 обрабатывает этот сигнал и закрывает сервер. После проверки учётных данных Compose запускает приложение через `exec hass-configurator`, передавая `/config/settings.conf`, если файл существует. Промежуточная оболочка из `/app/run.sh` образа не остаётся главным процессом, поэтому сигнал доходит до приложения. См. [скрипт образа](https://github.com/CausticLab/hass-configurator-docker/blob/master/run.sh) и [обработчик сигналов Configurator 0.6.0](https://github.com/danielperna84/hass-configurator/blob/0.6.0/hass_configurator/configurator.py).

После обновления Compose пересоздайте только File Editor из каталога `plugins/` серверного клона:

```bash
docker compose up -d --pull never --no-deps --force-recreate hass-configurator
```

Первое пересоздание старого контейнера ещё может потребовать прежнего таймаута остановки; новый контейнер уже получит исправленную команду и сигнал. Простой `docker restart` не применяет эти изменения.

Код `137` означает завершение через `SIGKILL` и сам по себе не доказывает нехватку памяти. Если остановка занимает примерно 60 секунд, вероятно, Docker дождался таймаута управляющего скрипта и принудительно завершил процесс. Сразу после остановки, до последующего запуска, состояние можно проверить так:

```bash
docker inspect --format 'exit={{.State.ExitCode}} oom={{.State.OOMKilled}} error={{.State.Error}}' hass-configurator
```

`oom=true` требует проверки нехватки памяти на устройстве. При `oom=false` и длительном ожидании на остановке проверяйте доставку сигнала и таймаут. После повторного запуска эти поля уже могут не отражать прежнее завершение.
