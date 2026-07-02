#!/bin/bash
PROXY_IP="82.21.117.170"
PROXY_PORT="8443"

echo "🔍 Проверяем статус ДО смены:"
sudo systemctl status mtproto-proxy.service --no-pager -l | head -20

# Генерируем новый 16-байтный hex секрет (32 символа)
NEW_SECRET=$(head -c 16 /dev/urandom | xxd -ps)
echo "🆕 Новый секрет: $NEW_SECRET"

# 1. Останавливаем сервис
echo "🛑 Останавливаем..."
sudo systemctl stop mtproto-proxy.service

# 2. ✅ МЕНЯЕМ СЕКРЕТ В systemd-сервисе (ТОЧНО ТВОЯ СТРОКА)
SERVICE_FILE="/etc/systemd/system/mtproto-proxy.service"
sudo sed -i "s/-S [0-9a-f]\{32\}/-S $NEW_SECRET/g" $SERVICE_FILE

# 3. Обновляем systemd
sudo systemctl daemon-reload

# 4. Запускаем с НОВЫМ секретом
echo "🔄 Запускаем с новым секретом..."
sudo systemctl start mtproto-proxy.service

# 5. Ждём и проверяем
sleep 5
echo "✅ Статус ПОСЛЕ смены:"
sudo systemctl status mtproto-proxy.service --no-pager -l | head -20

# 6. Проверяем процесс (должен показывать НОВЫЙ секрет)
echo "🔍 Новый процесс:"
ps aux | grep mtproto-proxy | grep -v grep

echo ""
echo "🎉 ✅ УСПЕШНО! ЕДИНСТВЕННАЯ НОВАЯ ССЫЛКА ДЛЯ ВСЕХ:"
echo "https://t.me/proxy?server=$PROXY_IP&port=$PROXY_PORT&secret=$NEW_SECRET"
echo "tg://proxy?server=$PROXY_IP&port=$PROXY_PORT&secret=$NEW_SECRET"
echo ""
echo "⚠️  СТАРЫЕ ССЫЛКИ НЕ РАБОТАЮТ! Рассылай ЭТУ новую всем пользователям."
