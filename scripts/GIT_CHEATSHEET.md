# Git Workflow — Шпаргалка

## Где я?
git branch          # звёздочка = ты тут

## Ежедневная работа
git checkout dev                          # начал кодить
git add . && git commit -m "что сделал"   # сохранил
# ... можно коммитить сколько угодно раз ...

## Готов к деплою?
sh scripts/merge.sh "описание"            # dev → main + автотег
sh scripts/deploy.sh                       # main → Cloud Run

## После деплоя
git checkout dev                           # обратно кодить

## Прод сломался?
sh scripts/deploy.sh --rollback            # откат за 2 минуты

## Посмотреть теги (версии)
git tag -l --sort=-v:refname               # все версии, новые сверху

## Золотое правило
# НИКОГДА не кодь на main. Всегда: git checkout dev