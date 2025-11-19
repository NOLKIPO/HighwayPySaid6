import os
import sys
import json
import time
import threading
import queue
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPushButton, QTableWidget, QTableWidgetItem, QLabel, QHeaderView,
    QFileDialog, QMessageBox, QTextEdit, QAbstractItemView,
    QDialog, QGridLayout, QLineEdit, QRadioButton, QButtonGroup,
    QSplitter, QSizePolicy, QToolButton, QScrollArea, QSpacerItem
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont
import paramiko
from paramiko import Ed25519Key
import PyQt6.uic # <--- Добавлен импорт uic

# === Пути ===
APP_DIR = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, 'frozen', False) else __file__))
CONFIG_PATH = os.path.join(APP_DIR, 'workspaces.json')
os.makedirs(APP_DIR, exist_ok=True)

# === Вспомогательные функции ===
def load_config():
    if not os.path.exists(CONFIG_PATH):
        default = {
            "workspaces": {}
        }
        save_config(default)
        return default
    with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)

def save_config(data):
    with open(CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

# === Клиентская логика (SFTPWorker) ===
class SFTPWorker:
    def __init__(self, config_dict, log_callback):
        self.config = config_dict
        self.log_callback = log_callback
        self.stop_event = threading.Event()
        self.mode = config_dict.get('mode', 'client')

    def log(self, msg):
        if self.log_callback:
            self.log_callback(f"[{time.strftime('%H:%M:%S')}] {msg}")

    def run(self):
        try:
            client_id = self.config['client_id']
            workspace = self.config['workspace']
            ssh_host = self.config['ssh_host']
            ssh_port = int(self.config['ssh_port'])
            ssh_key = self.get_ssh_key_path()  # <--- Теперь через метод
            poll_interval = int(self.config.get('poll_interval', 5))
            incoming_local = self.get_incoming_path()  # <--- Теперь через метод
            outgoing_local = self.get_outgoing_path()  # <--- Теперь через метод
            meta_dir = self.get_meta_path()  # <--- Теперь через метод
            os.makedirs(incoming_local, exist_ok=True)
            os.makedirs(outgoing_local, exist_ok=True)
            sent_dir = os.path.join(meta_dir, 'sent')
            os.makedirs(sent_dir, exist_ok=True)
            username = f"{client_id}-{workspace}"
            if not os.path.exists(ssh_key):
                self.log(f"? SSH-ключ не найден: {ssh_key}")
                return
            self.log(f"[OK] {self.mode} запущен: {username}")
            self.log(f" Интервал опроса: {poll_interval} сек")
            while not self.stop_event.is_set():
                try:
                    key = Ed25519Key(filename=ssh_key)
                    ssh = paramiko.SSHClient()
                    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    ssh.connect(ssh_host, port=ssh_port, username=username, pkey=key, timeout=10)
                    sftp = ssh.open_sftp()
                    if self.mode == 'client':
                        self.process_incoming(sftp, incoming_local, sent_dir, 'in')
                        self.process_outgoing(sftp, outgoing_local, sent_dir, 'out')
                    elif self.mode == 'processor':
                        self.process_incoming(sftp, incoming_local, sent_dir, 'out')
                        self.process_outgoing(sftp, outgoing_local, sent_dir, 'in')
                    elif self.mode == 'client-sign':
                        self.process_incoming(sftp, incoming_local, sent_dir, 'in')
                        self.process_outgoing(sftp, outgoing_local, sent_dir, 'visa')
                    elif self.mode == 'processor-sign':
                        self.process_incoming(sftp, incoming_local, sent_dir, 'visa')
                        self.process_outgoing(sftp, outgoing_local, sent_dir, 'out')
                    sftp.close()
                    ssh.close()
                except Exception as e:
                    self.log(f" Ошибка: {e}")
                finally:
                    if not self.stop_event.is_set():
                        time.sleep(poll_interval)
        except Exception as e:
            self.log(f"? Критическая ошибка: {e}")

    def get_incoming_path(self):
        client_id = self.config['client_id']
        workspace = self.config['workspace']
        return os.path.join(APP_DIR, client_id, workspace, "incoming")

    def get_outgoing_path(self):
        client_id = self.config['client_id']
        workspace = self.config['workspace']
        return os.path.join(APP_DIR, client_id, workspace, "outgoing")

    def get_meta_path(self):
        client_id = self.config['client_id']
        workspace = self.config['workspace']
        return os.path.join(APP_DIR, client_id, workspace, ".meta")

    def get_ssh_key_path(self):
        client_id = self.config['client_id']
        workspace = self.config['workspace']
        return os.path.join(APP_DIR, client_id, workspace, "key", f"{client_id}-{workspace}")

    def process_incoming(self, sftp, incoming_local, sent_dir, remote_subdir):
        try:
            remote_files = set(sftp.listdir(remote_subdir))
        except Exception as e:
            self.log(f"?? Не удалось прочитать /{remote_subdir}: {e}")
            return
        local_files = set(os.listdir(incoming_local))
        for f in list(remote_files):
            received_marker = os.path.join(sent_dir, f"{f}.received")
            if os.path.exists(received_marker):
                if f not in local_files:
                    try:
                        sftp.remove(f'{remote_subdir}/{f}')
                        os.remove(received_marker)
                        self.log(f" Удалён с сервера (подтверждён): {f}")
                    except Exception as e:
                        self.log(f"? Ошибка удаления {f}: {e}")
        for f in remote_files:
            received_marker = os.path.join(sent_dir, f"{f}.received")
            if f not in local_files and not os.path.exists(received_marker):
                try:
                    local_path = os.path.join(incoming_local, f)
                    sftp.get(f'{remote_subdir}/{f}', local_path)
                    self.log(f"?? Получен: {f}")
                    with open(received_marker, 'w') as fp:
                        fp.write(f"{time.time()}\n")
                except Exception as e:
                    self.log(f"? Ошибка получения {f}: {e}")

    def process_outgoing(self, sftp, outgoing_local, sent_dir, remote_subdir):
        try:
            remote_files = set(sftp.listdir(remote_subdir))
        except Exception as e:
            self.log(f"?? Не удалось прочитать /{remote_subdir}: {e}")
            return
        local_files = set(os.listdir(outgoing_local))
        for f in list(local_files):
            sent_marker = os.path.join(sent_dir, f"{f}.sent")
            if os.path.exists(sent_marker) and f not in remote_files:
                try:
                    os.remove(os.path.join(outgoing_local, f))
                    os.remove(sent_marker)
                    self.log(f" Подтверждён и удалён: {f}")
                except Exception as e:
                    self.log(f"? Ошибка удаления {f}: {e}")
        current_local = set(os.listdir(outgoing_local))
        for f in current_local:
            sent_marker = os.path.join(sent_dir, f"{f}.sent")
            if not os.path.exists(sent_marker):
                try:
                    sftp.put(os.path.join(outgoing_local, f), f'{remote_subdir}/{f}')
                    with open(sent_marker, 'w') as fp:
                        fp.write(f"{time.time()}\n")
                    self.log(f" Отправлен: {f}")
                except Exception as e:
                    self.log(f"? Ошибка отправки {f}: {e}")

# === Основное окно ===
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        # Загружаем UI из файла
        PyQt6.uic.loadUi('main_window.ui', self) # <--- Загружаем .ui файл

        # Устанавливаем фиксированный размер окна, если нужно
        self.resize(1200, 800)

        # Инициализируем внутренние переменные
        self.workers = {}
        self.log_queue = queue.Queue()
        self.currently_selected_key = "" # Для отслеживания текущей строки

        # === Настройка элементов интерфейса ===
        # Настройка заголовков таблицы
        header = self.table.horizontalHeader()
        header.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
        header.setStyleSheet("QHeaderView::section { background-color: #f0f0f0; }")
        self.table.verticalHeader().setVisible(False)
        # Выравнивание по центру
        for i in range(5): # 5 колонок: №, Client ID, Рабочее место, Статус, Режим
            self.table.horizontalHeaderItem(i).setTextAlignment(Qt.AlignmentFlag.AlignCenter)

        # === Группа для радиокнопок режима ===
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.mode_client_rb, 0)
        self.mode_group.addButton(self.mode_processor_rb, 1)
        self.mode_group.addButton(self.mode_client_sign_rb, 2)
        self.mode_group.addButton(self.mode_processor_sign_rb, 3)

        # === Подключение сигналов ===
        self.add_btn.clicked.connect(self.add_workspace)
        self.stop_all_btn.clicked.connect(self.stop_all_workers)
        self.table.currentItemChanged.connect(self.on_table_item_changed) # <--- Новый сигнал
        self.save_btn.clicked.connect(self.save_current_workspace)
        self.delete_btn.clicked.connect(self.delete_current_workspace)
        self.start_stop_btn.clicked.connect(self.toggle_current_worker)
        self.generate_key_btn.clicked.connect(self.generate_current_key)
        # Подключаем обновление путей при изменении Client ID или Workspace
        self.client_id_edit.textChanged.connect(self.update_paths_for_current)
        self.workspace_edit.textChanged.connect(self.update_paths_for_current)

        # === Загрузка конфигурации ===
        config = load_config()
        self.workspaces = config.get("workspaces", {})
        self.refresh_table()

        # === Таймер для логов ===
        self.log_timer = QTimer()
        self.log_timer.timeout.connect(self._poll_logs)
        self.log_timer.start(100)

    def log_callback(self, msg):
        self.log_queue.put(msg)

    def _poll_logs(self):
        try:
            while True:
                msg = self.log_queue.get_nowait()
                self.log_text.append(msg)
                self.log_text.verticalScrollBar().setValue(self.log_text.verticalScrollBar().maximum())
        except queue.Empty:
            pass

    def refresh_table(self):
        self.table.setRowCount(0) # Очищаем таблицу
        # Сортировка по ключам для постоянного порядка
        sorted_items = sorted(self.workspaces.items(), key=lambda x: x[0])
        self.table.setRowCount(len(sorted_items))
        for row, (key, ws) in enumerate(sorted_items):
            client_id = ws.get("client_id", "")
            workspace = ws.get("workspace", "")
            status = "✅ Запущен" if key in self.workers else "❌ Остановлен"
            mode = ws.get("mode", "client")

            # Номер строки (начинается с 1)
            num_item = QTableWidgetItem(str(row + 1))
            num_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)

            # Установка текста с выравниванием по центру
            for col, text in enumerate([num_item, client_id, workspace, status, mode]):
                item = QTableWidgetItem(text)
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                
                # Устанавливаем цвет фона для статуса
                if col == 3:  # Столбец статуса
                    if "Запущен" in text:
                        item.setBackground("#d5f4e6")  # Светло-зеленый для запущенных
                    else:
                        item.setBackground("#f4d5d5")  # Светло-красный для остановленных
                
                self.table.setItem(row, col, item)

        # Автоподбор ширины
        self.table.resizeColumnsToContents()

    def on_table_item_changed(self, current, previous):
        """Вызывается при изменении выделения строки в таблице."""
        if current is None:
            # Если строка не выбрана, очищаем правую панель
            self.clear_edit_panel()
            self.currently_selected_key = ""
            return

        row = current.row()
        # Находим ключ по номеру строки
        sorted_keys = sorted(self.workspaces.keys())
        if 0 <= row < len(sorted_keys):
            key = sorted_keys[row]
            self.currently_selected_key = key
            self.load_workspace_to_panel(key)
        else:
            self.clear_edit_panel()
            self.currently_selected_key = ""

    def clear_edit_panel(self):
        """Очищает правую панель редактирования."""
        self.client_id_edit.clear()
        self.workspace_edit.clear()
        self.host_edit.clear()
        self.port_edit.clear()
        self.interval_edit.clear()
        self.incoming_edit.clear()
        self.outgoing_edit.clear()
        self.meta_edit.clear()
        self.key_edit.clear()
        # Сбрасываем радиокнопки
        self.mode_group.setExclusive(False)
        for rb in self.mode_group.buttons():
            rb.setChecked(False)
        self.mode_group.setExclusive(True)

    def load_workspace_to_panel(self, key):
        """Загружает данные рабочего места в правую панель."""
        if key not in self.workspaces:
            self.clear_edit_panel()
            return

        ws = self.workspaces[key]
        self.client_id_edit.setText(ws.get("client_id", ""))
        self.workspace_edit.setText(ws.get("workspace", ""))
        self.host_edit.setText(ws.get("ssh_host", ""))
        self.port_edit.setText(str(ws.get("ssh_port", 22)))
        self.interval_edit.setText(str(ws.get("poll_interval", 5)))

        # Устанавливаем нужный режим
        mode = ws.get("mode", "client")
        modes = {"client": self.mode_client_rb, "processor": self.mode_processor_rb,
                 "client-sign": self.mode_client_sign_rb, "processor-sign": self.mode_processor_sign_rb}
        if mode in modes:
            modes[mode].setChecked(True)

        # Обновляем пути
        self.update_paths_for_current()

        # Обновляем кнопку старт/стоп
        if key in self.workers:
            self.start_stop_btn.setText("◼ Остановить")
        else:
            self.start_stop_btn.setText("▶ Запустить")

    def update_paths_for_current(self):
        """Обновляет пути в правой панели на основе Client ID и Workspace."""
        client_id = self.client_id_edit.text().strip()
        workspace = self.workspace_edit.text().strip()
        if client_id and workspace:
            ws_dir = os.path.join(APP_DIR, client_id, workspace)
            incoming_path = os.path.join(ws_dir, "incoming")
            outgoing_path = os.path.join(ws_dir, "outgoing")
            meta_path = os.path.join(ws_dir, ".meta")
            key_path = os.path.join(ws_dir, "key", f"{client_id}-{workspace}")
        else:
            incoming_path = ""
            outgoing_path = ""
            meta_path = ""
            key_path = ""
        self.incoming_edit.setText(incoming_path)
        self.outgoing_edit.setText(outgoing_path)
        self.meta_edit.setText(meta_path)
        self.key_edit.setText(key_path)

    def save_current_workspace(self):
        """Сохраняет текущее рабочее место из правой панели."""
        if not self.currently_selected_key:
            QMessageBox.warning(self, "Ошибка", "Нет выбранного рабочего места для сохранения.")
            return

        original_config = self.workspaces[self.currently_selected_key].copy()

        try:
            client_id = self.client_id_edit.text().strip().replace(' ', '_')
            workspace = self.workspace_edit.text().strip().replace(' ', '_')
            if not client_id or not workspace:
                QMessageBox.critical(self, "Ошибка", "Client ID и Рабочее место не могут быть пустыми.")
                return

            new_key = f"{client_id}_{workspace}"

            # Проверяем, запущен ли старый worker
            worker_running = self.currently_selected_key in self.workers

            # Обновляем конфиг
            self.workspaces[self.currently_selected_key].update({
                "client_id": client_id,
                "workspace": workspace,
                "ssh_host": self.host_edit.text().strip(),
                "ssh_port": int(self.port_edit.text().strip()),
                "poll_interval": int(self.interval_edit.text().strip()),
                "mode": self.get_current_mode()
            })

            # Если ключ изменился (client_id или workspace)
            if new_key != self.currently_selected_key:
                # Создаём новые каталоги
                ws_dir = os.path.join(APP_DIR, client_id, workspace)
                os.makedirs(ws_dir, exist_ok=True)
                os.makedirs(os.path.join(ws_dir, "incoming"), exist_ok=True)
                os.makedirs(os.path.join(ws_dir, "outgoing"), exist_ok=True)
                os.makedirs(os.path.join(ws_dir, ".meta"), exist_ok=True)
                os.makedirs(os.path.join(ws_dir, "key"), exist_ok=True)

                # Перемещаем запись
                self.workspaces[new_key] = self.workspaces.pop(self.currently_selected_key)

                # Перемещаем worker, если он был запущен
                if worker_running:
                    self.workers[new_key] = self.workers.pop(self.currently_selected_key)

                # Обновляем текущий ключ
                self.currently_selected_key = new_key

            save_config({"workspaces": self.workers}) # <--- Исправлено: workspaces, а не workers
            self.refresh_table()
            # Обновляем правую панель с новым ключом, если он изменился
            if self.currently_selected_key in self.workspaces:
                 self.load_workspace_to_panel(self.currently_selected_key)
            self.log_callback("Конфигурация сохранена")
        except ValueError as e:
            QMessageBox.critical(self, "Ошибка", f"Неверные числовые данные:\n{e}")
            # Восстанавливаем старую конфигурацию
            self.workspaces[self.currently_selected_key] = original_config
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", f"Неверные данные:\n{e}")
            # Восстанавливаем старую конфигурацию
            self.workspaces[self.currently_selected_key] = original_config


    def delete_current_workspace(self):
        """Удаляет текущее рабочее место."""
        if not self.currently_selected_key:
            QMessageBox.warning(self, "Ошибка", "Нет выбранного рабочего места для удаления.")
            return

        if QMessageBox.question(self, "Удаление", f"Удалить '{self.currently_selected_key}'?") == QMessageBox.StandardButton.Yes:
            if self.currently_selected_key in self.workers:
                self.stop_worker(self.currently_selected_key)
            del self.workspaces[self.currently_selected_key]
            save_config({"workspaces": self.workspaces})  # <--- Сохраняем сразу
            self.refresh_table()
            self.clear_edit_panel()
            self.currently_selected_key = ""
            self.log_callback(f"Рабочее пространство '{self.currently_selected_key}' удалено.")

    def toggle_current_worker(self):
        """Запускает или останавливает worker для текущего рабочего места."""
        if not self.currently_selected_key:
            return

        if self.currently_selected_key in self.workers:
            self.stop_worker(self.currently_selected_key)
        else:
            self.start_worker(self.currently_selected_key)

    def start_worker(self, key):
        if key in self.workers or key not in self.workspaces:
            return
        ws = self.workspaces[key]
        # Создаём worker с динамическими путями
        worker = SFTPWorker(ws, self.log_callback)
        thread = threading.Thread(target=worker.run, daemon=True)
        self.workers[key] = (thread, worker)
        thread.start()
        self.refresh_table()
        # Обновляем кнопку в правой панели
        if key == self.currently_selected_key:
            self.start_stop_btn.setText("◼ Остановить")

    def stop_worker(self, key):
        if key in self.workers:
            thread, worker = self.workers[key]
            worker.stop_event.set()
            thread.join(timeout=5)
            del self.workers[key]
        self.refresh_table()
        # Обновляем кнопку в правой панели
        if key == self.currently_selected_key:
            self.start_stop_btn.setText("▶ Запустить")

    def stop_all_workers(self):
        for key in list(self.workers.keys()):
            self.stop_worker(key)
        self.log_callback("Все сервисы остановлены")

    def add_workspace(self):
        # Открываем диалог добавления с пустыми полями
        new_config = {
            "client_id": "",
            "workspace": "",
            "ssh_host": "without.su",
            "ssh_port": 22,
            "mode": "client",
            "poll_interval": 5
        }
        key = f"new_{len(self.workspaces)}" # Временный ключ
        self.workspaces[key] = new_config
        save_config({"workspaces": self.workspaces})
        self.refresh_table()
        # Найдем новую строку и выберем её
        sorted_keys = sorted(self.workspaces.keys())
        try:
            new_row = sorted_keys.index(key)
            self.table.selectRow(new_row)
            # После выбора строки, она загрузится в панель редактирования
            # Теперь очистим поля и установим временные значения
            self.client_id_edit.setText(new_config["client_id"])
            self.workspace_edit.setText(new_config["workspace"])
            self.host_edit.setText(new_config["ssh_host"])
            self.port_edit.setText(str(new_config["ssh_port"]))
            self.interval_edit.setText(str(new_config["poll_interval"]))
            self.mode_client_rb.setChecked(True) # Установим первый режим
            self.update_paths_for_current() # Обновим пути
            self.currently_selected_key = key # Обновим текущий ключ
            self.start_stop_btn.setText("▶ Запустить") # Обновим кнопку
        except ValueError:
            pass # Если ключ не найден (маловероятно)


    def get_current_mode(self):
        """Возвращает текущий выбранный режим из радиокнопок."""
        mode_id = self.mode_group.checkedId()
        modes = ["client", "processor", "client-sign", "processor-sign"]
        return modes[mode_id] if mode_id != -1 else "client"

    def generate_current_key(self):
        """Генерирует ключ для текущего рабочего места."""
        client_id = self.client_id_edit.text().strip()
        workspace = self.workspace_edit.text().strip()
        if not client_id or not workspace:
            QMessageBox.warning(self, "Ошибка", "Укажите Client ID и Рабочее место.")
            return
        key_name = f"{client_id}-{workspace}"
        key_dir = os.path.join(APP_DIR, client_id, workspace, "key")
        os.makedirs(key_dir, exist_ok=True)
        key_path = os.path.join(key_dir, key_name)
        if os.path.exists(key_path):
            if QMessageBox.question(self, "Подтверждение", f"Ключ {key_name} уже существует. Перезаписать?") != QMessageBox.StandardButton.Yes:
                return
        if QMessageBox.question(self, "Подтверждение", f"Сгенерировать новый ключ {key_name}?") == QMessageBox.StandardButton.Yes:
            try:
                key = Ed25519Key.generate()
                with open(key_path, 'w') as f:
                    key.write_private_key(f)
                self.key_edit.setText(key_path)
                QMessageBox.information(self, "Успех", f"Ключ '{key_name}' сгенерирован и сохранён в:\n{key_path}")
            except Exception as e:
                QMessageBox.critical(self, "Ошибка", f"Не удалось сгенерировать ключ:\n{e}")

    def closeEvent(self, event):
        for key in list(self.workers.keys()):
            self.stop_worker(key)
        event.accept()

def load_stylesheet():
    """Загружает файл стилей из файла styles.css"""
    try:
        with open('styles.css', 'r', encoding='utf-8') as f:
            return f.read()
    except FileNotFoundError:
        return ""


if __name__ == "__main__":
    app = QApplication(sys.argv)
    
    # Применяем стили
    stylesheet = load_stylesheet()
    if stylesheet:
        app.setStyleSheet(stylesheet)
    
    win = MainWindow()
    win.show()
    sys.exit(app.exec())