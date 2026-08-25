"""Lightweight runtime UI translation: English/Russian/Ukrainian, no Qt
Linguist / .ts-.qm toolchain.

English source strings are the dict keys, so anything missing from
`_STRINGS` (or a language not in `LANGUAGES`) just falls back to English
instead of erroring. Widgets that must update immediately when the user
switches language (page titles, button text, form labels, group boxes)
call `tr()` from a `retranslate_ui()` method and connect that method to
`translator.language_changed`; widgets populated from slow/expensive
scans (extracted edition lists, Appx package lists) are re-rendered with
`tr()` next time `initializePage()` runs rather than instantly, since that
data is already cached and QWizard re-runs `initializePage()` on every
visit to a page.
"""

from __future__ import annotations

from PySide6.QtCore import QObject, QSettings, Signal
from PySide6.QtWidgets import QComboBox, QHBoxLayout, QLabel, QWidget

LANGUAGES: dict[str, str] = {
    "en": "English",
    "ru": "Русский",
    "uk": "Українська",
}

_DEFAULT_LANGUAGE = "en"

_SETTINGS_ORG = "WindowsCustomizationToolkit"
_SETTINGS_APP = "ISOCustomizer"
_SETTINGS_KEY = "language"

# English source text -> {language code: translation}.
_STRINGS: dict[str, dict[str, str]] = {
    "Administrator privileges required": {
        "ru": "Требуются права администратора",
        "uk": "Потрібні права адміністратора",
    },
    "This tool needs Administrator privileges for DISM and registry "
    "operations. You can browse the wizard, but the build step will "
    "fail until you re-launch this app as Administrator.": {
        "ru": "Этой программе нужны права администратора для операций DISM и работы "
        "с реестром. Вы можете просматривать мастер, но шаг сборки завершится "
        "ошибкой, пока вы не перезапустите приложение от имени администратора.",
        "uk": "Цій програмі потрібні права адміністратора для операцій DISM та роботи "
        "з реєстром. Ви можете переглядати майстер, але крок збирання завершиться "
        "помилкою, поки ви не перезапустите застосунок від імені адміністратора.",
    },
    "Windows ISO Customizer": {
        "ru": "Кастомизатор Windows ISO",
        "uk": "Кастомізатор Windows ISO",
    },
    "Load preset...": {"ru": "Загрузить пресет...", "uk": "Завантажити пресет..."},
    "Save preset...": {"ru": "Сохранить пресет...", "uk": "Зберегти пресет..."},
    "Load preset": {"ru": "Загрузка пресета", "uk": "Завантаження пресета"},
    "Could not load preset": {"ru": "Не удалось загрузить пресет", "uk": "Не вдалося завантажити пресет"},
    "Password not restored": {"ru": "Пароль не восстановлен", "uk": "Пароль не відновлено"},
    "This preset has a local account configured, but its password is "
    "never saved to disk - re-enter it on the Customization page.": {
        "ru": "В этом пресете настроена локальная учётная запись, но её пароль "
        "никогда не сохраняется на диск - введите его заново на странице «Настройка».",
        "uk": "У цьому пресеті налаштовано локальний обліковий запис, але його пароль "
        "ніколи не зберігається на диск - введіть його знову на сторінці «Налаштування».",
    },
    "Save preset": {"ru": "Сохранение пресета", "uk": "Збереження пресета"},
    "Could not save preset": {"ru": "Не удалось сохранить пресет", "uk": "Не вдалося зберегти пресет"},
    "Interface language": {"ru": "Язык интерфейса", "uk": "Мова інтерфейсу"},
    # SourcePage
    "Source ISO and working directory": {
        "ru": "Исходный ISO и рабочий каталог",
        "uk": "Вихідний ISO та робочий каталог",
    },
    "Pick the original Windows ISO, a working directory with enough free "
    "space, and where to save the finished ISO.": {
        "ru": "Выберите исходный ISO-образ Windows, рабочий каталог с достаточным "
        "объёмом свободного места и место сохранения готового ISO.",
        "uk": "Виберіть вихідний ISO-образ Windows, робочий каталог із достатнім "
        "обсягом вільного місця та місце збереження готового ISO.",
    },
    "Browse...": {"ru": "Обзор...", "uk": "Огляд..."},
    "Source ISO:": {"ru": "Исходный ISO:", "uk": "Вихідний ISO:"},
    "Working directory:": {"ru": "Рабочий каталог:", "uk": "Робочий каталог:"},
    "Output ISO:": {"ru": "Итоговый ISO:", "uk": "Підсумковий ISO:"},
    "Select Windows ISO": {"ru": "Выберите ISO-образ Windows", "uk": "Виберіть ISO-образ Windows"},
    "Select working directory": {"ru": "Выберите рабочий каталог", "uk": "Виберіть робочий каталог"},
    "Save custom ISO as": {"ru": "Сохранить итоговый ISO как", "uk": "Зберегти підсумковий ISO як"},
    "Not enough free space at {path}: {free} GB free, need roughly {required} GB "
    "(4x the ISO size - WIM mount/servicing needs headroom).": {
        "ru": "Недостаточно свободного места в {path}: свободно {free} ГБ, требуется "
        "примерно {required} ГБ (4x размера ISO - монтированию/обслуживанию WIM нужен запас).",
        "uk": "Недостатньо вільного місця в {path}: вільно {free} ГБ, потрібно приблизно "
        "{required} ГБ (4x розміру ISO - монтуванню/обслуговуванню WIM потрібен запас).",
    },
    "{free} GB free at {path} - OK": {
        "ru": "{free} ГБ свободно в {path} - ОК",
        "uk": "{free} ГБ вільно в {path} - ОК",
    },
    # EditionPage
    "Select Windows edition": {"ru": "Выбор редакции Windows", "uk": "Вибір редакції Windows"},
    "The ISO is extracted once here; pick which edition/index to customize.": {
        "ru": "Здесь ISO извлекается один раз; выберите редакцию/индекс для настройки.",
        "uk": "Тут ISO видобувається один раз; виберіть редакцію/індекс для налаштування.",
    },
    "Extracting ISO...": {"ru": "Извлечение ISO...", "uk": "Видобування ISO..."},
    "Reading edition list...": {"ru": "Чтение списка редакций...", "uk": "Читання списку редакцій..."},
    "Neither install.wim nor install.esd found under {path}": {
        "ru": "Не найден ни install.wim, ни install.esd в {path}",
        "uk": "Не знайдено ні install.wim, ні install.esd у {path}",
    },
    "Extraction failed": {"ru": "Ошибка извлечения", "uk": "Помилка видобування"},
    "Index {index}: {name} - {description} ({size})": {
        "ru": "Индекс {index}: {name} - {description} ({size})",
        "uk": "Індекс {index}: {name} - {description} ({size})",
    },
    "unknown size": {"ru": "размер неизвестен", "uk": "розмір невідомий"},
    "Converting install.esd to install.wim...": {
        "ru": "Преобразование install.esd в install.wim...",
        "uk": "Перетворення install.esd на install.wim...",
    },
    "Conversion failed": {"ru": "Ошибка преобразования", "uk": "Помилка перетворення"},
    # DebloatPage
    "Remove preinstalled apps (debloat)": {
        "ru": "Удаление предустановленных приложений (debloat)",
        "uk": "Видалення попередньо встановлених застосунків (debloat)",
    },
    "Only packages actually present in the selected edition are listed.": {
        "ru": "Показаны только пакеты, действительно присутствующие в выбранной редакции.",
        "uk": "Показано лише пакунки, які справді присутні у вибраній редакції.",
    },
    "Scanning image for installed apps...": {
        "ru": "Сканирование образа на предмет установленных приложений...",
        "uk": "Сканування образу на наявність встановлених застосунків...",
    },
    "Could not scan image": {"ru": "Не удалось просканировать образ", "uk": "Не вдалося просканувати образ"},
    "{exc}\n\nYou can continue without selecting any apps to remove; "
    "debloating will simply be skipped.": {
        "ru": "{exc}\n\nВы можете продолжить, не выбирая приложения для удаления; "
        "очистка будет просто пропущена.",
        "uk": "{exc}\n\nВи можете продовжити, не вибираючи застосунки для видалення; "
        "очищення буде просто пропущено.",
    },
    # CustomizePage
    "Customization": {"ru": "Настройка", "uk": "Налаштування"},
    "Registry tweaks, silent software installs, and Setup answer-file options.": {
        "ru": "Правки реестра, тихая установка ПО и параметры файла ответов для установки.",
        "uk": "Правки реєстру, тиха установка ПЗ та параметри файлу відповідей для встановлення.",
    },
    ".reg file tweaks": {"ru": "Правки из .reg файлов", "uk": "Правки з .reg файлів"},
    "Add .reg file...": {"ru": "Добавить .reg файл...", "uk": "Додати .reg файл..."},
    "Remove selected": {"ru": "Удалить выбранное", "uk": "Видалити вибране"},
    "Select .reg file": {"ru": "Выберите .reg файл", "uk": "Виберіть .reg файл"},
    "Target hive": {"ru": "Целевой куст реестра", "uk": "Цільовий кущ реєстру"},
    "Which offline hive does {name} target?": {
        "ru": "Какой offline-куст реестра затрагивает {name}?",
        "uk": "Який offline-кущ реєстру стосується {name}?",
    },
    "Silent software installs (run once at first boot)": {
        "ru": "Тихая установка ПО (запускается один раз при первой загрузке)",
        "uk": "Тиха установка ПЗ (запускається один раз під час першого завантаження)",
    },
    "Add installer...": {"ru": "Добавить установщик...", "uk": "Додати інсталятор..."},
    "Select installer": {"ru": "Выберите установщик", "uk": "Виберіть інсталятор"},
    "Silent install arguments": {"ru": "Аргументы тихой установки", "uk": "Аргументи тихого встановлення"},
    "Arguments to run {name} silently (e.g. /quiet /norestart):": {
        "ru": "Аргументы для тихого запуска {name} (например /quiet /norestart):",
        "uk": "Аргументи для тихого запуску {name} (наприклад /quiet /norestart):",
    },
    "Windows 11 setup checks (autounattend.xml)": {
        "ru": "Проверки установки Windows 11 (autounattend.xml)",
        "uk": "Перевірки встановлення Windows 11 (autounattend.xml)",
    },
    "Bypass TPM 2.0 check": {"ru": "Обойти проверку TPM 2.0", "uk": "Обійти перевірку TPM 2.0"},
    "Bypass Secure Boot check": {"ru": "Обойти проверку Secure Boot", "uk": "Обійти перевірку Secure Boot"},
    "Bypass RAM check": {"ru": "Обойти проверку объёма ОЗУ", "uk": "Обійти перевірку обсягу ОЗП"},
    "Bypass storage check": {
        "ru": "Обойти проверку объёма накопителя",
        "uk": "Обійти перевірку обсягу накопичувача",
    },
    "Bypass CPU check": {"ru": "Обойти проверку процессора", "uk": "Обійти перевірку процесора"},
    'Bypass "Microsoft account required" (BypassNRO)': {
        "ru": "Обойти требование учётной записи Microsoft (BypassNRO)",
        "uk": "Обійти вимогу облікового запису Microsoft (BypassNRO)",
    },
    "Create local account": {"ru": "Создать локальную учётную запись", "uk": "Створити локальний обліковий запис"},
    "Username:": {"ru": "Имя пользователя:", "uk": "Ім'я користувача:"},
    "Password:": {"ru": "Пароль:", "uk": "Пароль:"},
    "Group:": {"ru": "Группа:", "uk": "Група:"},
    "Store password as plaintext in autounattend.xml (not recommended)": {
        "ru": "Хранить пароль в открытом виде в autounattend.xml (не рекомендуется)",
        "uk": "Зберігати пароль у відкритому вигляді в autounattend.xml (не рекомендовано)",
    },
    "Regional settings": {"ru": "Региональные параметры", "uk": "Регіональні параметри"},
    "Input locale:": {"ru": "Раскладка ввода:", "uk": "Розкладка введення:"},
    "System locale:": {"ru": "Системный языковой стандарт:", "uk": "Системний мовний стандарт:"},
    "UI language:": {"ru": "Язык интерфейса Windows:", "uk": "Мова інтерфейсу Windows:"},
    "User locale:": {"ru": "Языковой стандарт пользователя:", "uk": "Мовний стандарт користувача:"},
    "Time zone:": {"ru": "Часовой пояс:", "uk": "Часовий пояс:"},
    "Computer name:": {"ru": "Имя компьютера:", "uk": "Ім'я комп'ютера:"},
    "Product key:": {"ru": "Ключ продукта:", "uk": "Ключ продукту:"},
    "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX (optional)": {
        "ru": "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX (необязательно)",
        "uk": "XXXXX-XXXXX-XXXXX-XXXXX-XXXXX (необов'язково)",
    },
    "ISO build tool": {"ru": "Инструмент сборки ISO", "uk": "Інструмент збирання ISO"},
    "Auto-detect": {"ru": "Определить автоматически", "uk": "Визначити автоматично"},
    "Invalid answer-file settings": {
        "ru": "Некорректные параметры файла ответов",
        "uk": "Некоректні параметри файлу відповідей",
    },
    # BuildPage
    "Build": {"ru": "Сборка", "uk": "Збирання"},
    "Review the summary below, then start the build. This can take several minutes.": {
        "ru": "Проверьте сводку ниже и запустите сборку. Это может занять несколько минут.",
        "uk": "Перевірте зведення нижче та запустіть збирання. Це може зайняти кілька хвилин.",
    },
    "Not started": {"ru": "Не запущено", "uk": "Не запущено"},
    "Start build": {"ru": "Начать сборку", "uk": "Почати збирання"},
    "Source:": {"ru": "Источник:", "uk": "Джерело:"},
    "Edition index:": {"ru": "Индекс редакции:", "uk": "Індекс редакції:"},
    "Apps to remove:": {"ru": "Приложений к удалению:", "uk": "Застосунків до видалення:"},
    "Registry tweaks:": {"ru": "Правок реестра:", "uk": "Правок реєстру:"},
    "Software installs:": {"ru": "Устанавливаемых программ:", "uk": "Програм для встановлення:"},
    "Orphaned image mount found": {
        "ru": "Обнаружено осиротевшее монтирование образа",
        "uk": "Знайдено осиротіле монтування образу",
    },
    "An existing DISM mount was found, likely left over from a previous "
    "run that didn't finish cleanly:\n\n"
    "Mount dir: {mount_dir}\nImage file: {image_file}\nStatus: {status}\n\n"
    "Commit it (Yes), discard it (No), or abort the build (Cancel)?": {
        "ru": "Найдено существующее монтирование DISM, вероятно оставшееся после "
        "предыдущего запуска, который не завершился корректно:\n\n"
        "Каталог монтирования: {mount_dir}\nФайл образа: {image_file}\nСтатус: {status}\n\n"
        "Применить (Да), отменить (Нет) или прервать сборку (Отмена)?",
        "uk": "Знайдено наявне монтування DISM, ймовірно, що залишилося після "
        "попереднього запуску, який не завершився коректно:\n\n"
        "Каталог монтування: {mount_dir}\nФайл образу: {image_file}\nСтатус: {status}\n\n"
        "Застосувати (Так), скасувати (Ні) чи перервати збирання (Скасувати)?",
    },
    "Build complete": {"ru": "Сборка завершена", "uk": "Збирання завершено"},
    "ISO built successfully:\n{path}": {
        "ru": "ISO успешно собран:\n{path}",
        "uk": "ISO успішно зібрано:\n{path}",
    },
    "Build failed": {"ru": "Ошибка сборки", "uk": "Помилка збирання"},
    # worker.py pipeline stage labels
    "Checking for orphaned mounts": {
        "ru": "Проверка осиротевших монтирований",
        "uk": "Перевірка осиротілих монтувань",
    },
    "Mounting image (index {index})": {
        "ru": "Монтирование образа (индекс {index})",
        "uk": "Монтування образу (індекс {index})",
    },
    "Writing unattend answer file": {
        "ru": "Запись файла ответов unattend",
        "uk": "Запис файлу відповідей unattend",
    },
    "Building ISO": {"ru": "Сборка ISO", "uk": "Збирання ISO"},
    "Verifying ISO": {"ru": "Проверка ISO", "uk": "Перевірка ISO"},
    "Done": {"ru": "Готово", "uk": "Готово"},
    "Removing provisioned Appx packages": {
        "ru": "Удаление предустановленных Appx-пакетов",
        "uk": "Видалення попередньо встановлених Appx-пакунків",
    },
    "Applying registry tweak {i}/{n}: {name}": {
        "ru": "Применение правки реестра {i}/{n}: {name}",
        "uk": "Застосування правки реєстру {i}/{n}: {name}",
    },
    "Staging installer {i}/{n}: {name}": {
        "ru": "Подготовка установщика {i}/{n}: {name}",
        "uk": "Підготовка інсталятора {i}/{n}: {name}",
    },
}


class Translator(QObject):
    """Global translation state. `language_changed` fires after `set_language()`
    so open windows can re-run their `retranslate_ui()` methods in place."""

    language_changed = Signal()

    def __init__(self) -> None:
        super().__init__()
        settings = QSettings(_SETTINGS_ORG, _SETTINGS_APP)
        saved = settings.value(_SETTINGS_KEY, _DEFAULT_LANGUAGE)
        self._language = saved if saved in LANGUAGES else _DEFAULT_LANGUAGE

    @property
    def language(self) -> str:
        return self._language

    def set_language(self, language: str) -> None:
        if language not in LANGUAGES or language == self._language:
            return
        self._language = language
        QSettings(_SETTINGS_ORG, _SETTINGS_APP).setValue(_SETTINGS_KEY, language)
        self.language_changed.emit()

    def tr(self, text: str) -> str:
        if self._language == "en":
            return text
        return _STRINGS.get(text, {}).get(self._language, text)


translator = Translator()


def tr(text: str) -> str:
    """Translate `text` (an English source string) to the current UI language."""
    return translator.tr(text)


class LanguageSelector(QWidget):
    """A `Label: [combo]` row bound to the global `translator`. Any page can
    embed one; picking a language in any instance retranslates every open
    page, since they all listen to the same `translator.language_changed`.
    """

    def __init__(self) -> None:
        super().__init__()
        self._label = QLabel()
        self._combo = QComboBox()
        for code, native_name in LANGUAGES.items():
            self._combo.addItem(native_name, code)
        self._combo.setCurrentIndex(self._combo.findData(translator.language))
        self._combo.currentIndexChanged.connect(self._on_combo_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._label)
        layout.addWidget(self._combo)
        layout.addStretch(1)

        translator.language_changed.connect(self._retranslate)
        self._retranslate()

    def _on_combo_changed(self, index: int) -> None:
        translator.set_language(self._combo.itemData(index))

    def _retranslate(self) -> None:
        self._label.setText(tr("Interface language") + ":")
        self._combo.blockSignals(True)
        self._combo.setCurrentIndex(self._combo.findData(translator.language))
        self._combo.blockSignals(False)
