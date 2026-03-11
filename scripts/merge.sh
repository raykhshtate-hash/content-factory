#!/bin/bash
# =============================================================
# merge.sh — Безопасный мерж dev → main с авто-тегом
# Использование: sh scripts/merge.sh "описание что сделал"
# =============================================================
set -e

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# --- Проверки ---
CURRENT=$(git branch --show-current)
if [ "$CURRENT" != "dev" ]; then
    echo -e "${RED}❌ Ты на ветке '$CURRENT'. Сначала: git checkout dev${NC}"
    exit 1
fi

# Проверяем что нет незакоммиченных изменений
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo -e "${RED}❌ Есть незакоммиченные изменения. Сначала:${NC}"
    echo "   git add . && git commit -m \"описание\""
    exit 1
fi

# --- Описание для тега ---
if [ -z "$1" ]; then
    echo -e "${YELLOW}Что сделано? (для тега, одной строкой):${NC}"
    read -r TAG_MSG
else
    TAG_MSG="$1"
fi

if [ -z "$TAG_MSG" ]; then
    echo -e "${RED}❌ Нужно описание. Пример: sh scripts/merge.sh \"emoji sanitizer done\"${NC}"
    exit 1
fi

# --- Генерим версию ---
# Находим последний тег вида v0.X.Y и инкрементим patch
LAST_TAG=$(git tag -l "v*" --sort=-v:refname | head -1)
if [ -z "$LAST_TAG" ]; then
    NEW_TAG="v0.1.0"
else
    # Парсим major.minor.patch
    MAJOR=$(echo "$LAST_TAG" | sed 's/v//' | cut -d. -f1)
    MINOR=$(echo "$LAST_TAG" | sed 's/v//' | cut -d. -f2)
    PATCH=$(echo "$LAST_TAG" | sed 's/v//' | cut -d. -f3)
    PATCH=$((PATCH + 1))
    NEW_TAG="v${MAJOR}.${MINOR}.${PATCH}"
fi

# --- Мерж ---
echo -e "${YELLOW}🔀 Мержу dev → main...${NC}"
git checkout main
git merge dev --no-edit

# --- Тег ---
git tag -a "$NEW_TAG" -m "$TAG_MSG"

echo ""
echo -e "${GREEN}✅ Готово!${NC}"
echo -e "   Ветка: main"
echo -e "   Тег:   ${GREEN}${NEW_TAG}${NC} — $TAG_MSG"
echo -e "   Предыдущий: ${LAST_TAG:-нет}"
echo ""
echo -e "${YELLOW}Следующий шаг: sh scripts/deploy.sh${NC}"
echo -e "Или вернуться кодить: git checkout dev"
