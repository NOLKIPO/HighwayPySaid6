#!/usr/bin/env python3
"""
Тестовый скрипт для проверки корректности запуска приложения
"""
import sys
import os
import time
import signal
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer

# Добавляем путь к проекту
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main_pysaid import MainWindow

def test_app():
    app = QApplication(sys.argv)
    
    # Создаем главное окно
    window = MainWindow()
    print("MainWindow успешно создан")
    
    # Проверяем, что интерфейс загружен
    assert hasattr(window, 'table'), "Таблица не найдена в интерфейсе"
    assert hasattr(window, 'add_btn'), "Кнопка добавления не найдена"
    assert hasattr(window, 'start_stop_btn'), "Кнопка запуска/останова не найдена"
    print("Элементы интерфейса успешно найдены")
    
    # Показываем окно
    window.show()
    print("Окно успешно показано")
    
    # Проверяем, что таблица обновляется без ошибок
    window.refresh_table()
    print("Таблица успешно обновлена")
    
    # Завершаем приложение через 2 секунды
    def quit_app():
        print("Завершаем тестовое приложение...")
        app.quit()
    
    QTimer.singleShot(2000, quit_app)
    
    # Запускаем цикл событий
    sys.exit(app.exec())

if __name__ == "__main__":
    test_app()