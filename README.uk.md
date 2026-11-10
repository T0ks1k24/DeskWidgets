# DeskWidgets — віджети робочого столу

**Віджети в стилі macOS для Linux і Windows**: годинники, календар, погода, моніторинг системи, «Зараз грає»,
події, нотатки, курс НБУ, Docker… і аніме-віджети. Вони стоять по сітці, мають три розміри та дев’ять
стилів із будь-яким кольором, у кожного свої налаштування, а свій віджет — це тека з двома файлами.

🇬🇧 [English](README.md)

![DeskWidgets на робочому столі](docs/screenshots/hero-uk.png)

## Можливості

* **21 віджет**: аналоговий і цифровий годинник, світовий час, календар (тиждень/місяць), погода
  (зараз, погодинно, на 3 дні), події (календарі GNOME, зокрема Google), «Зараз грає» (будь-який
  плеєр MPRIS, обкладинка, клік — пауза), система, процесор, пам’ять, диски, мережа (+ контроль
  невідомих пристроїв), батарея (+ Bluetooth-пристрої та ігрові миші Pulsar/Kysona на 2.4 ГГц), Docker, нотатка, відлік, картинка, курс НБУ і
  **аніме**: нові серії з AniList, манґа-годинник, аніме-фраза.
* **Сітка**: перетягніть віджет мишею — з’явиться сітка, і він стане в найближчу вільну клітинку.
* **Три розміри** для кожного віджета: маленький (1×1), середній (2×1), великий (2×2) і загальний
  масштаб віджетів від 80 % до 150 %.
* **Дев’ять стилів** — Скло, Графіт, Світлий, Кольоровий, Сакура, Манґа, Неон і без фону (темний чи
  світлий текст) — та **13 кольорів акценту**, що тонують картку й акценти.
* **Свої налаштування в кожного віджета** (місто, міста, валюти, текст, дати, картинки…) з живим
  прев’ю. Права кнопка миші на віджеті відкриває їх. Можна додати кілька однакових віджетів.
* **Свій віджет** пишеться на Lua з простим API для малювання — `deskwidgets new my_widget` створює
  робочу заготовку. Див. [docs/creating-widgets.md](docs/creating-widgets.md) (англійською).
* Інтерфейс українською та англійською. Працює у **Windows 10/11** та в Linux (X11 і Wayland через Xwayland).

![стилі](docs/screenshots/styles-uk.png)
![аніме](docs/screenshots/anime-uk.png)

## Встановлення

Потрібні Python 3.12+, [pycairo](https://pycairo.readthedocs.io), [lupa](https://github.com/scoder/lupa),
[psutil](https://github.com/giampaolo/psutil) і [Pillow](https://python-pillow.org); жодної іншої програми для віджетів.

### Windows 10 / 11

Встановіть [Python](https://www.python.org/downloads/windows/) (з пунктом «Add python.exe to PATH»), далі в терміналі:

```bat
pip install deskwidgets        :: або з клону репозиторію: pip install .
deskwidgets setup       :: автозапуск і пункт у меню «Пуск»
deskwidgets             :: запуск; галерею відкриває значок у треї
```

За бажанням: `pip install winsdk` додає віджет «Зараз грає» (будь-який плеєр, що видно в панелі гучності).
Віджети, яким потрібні служби Linux («Події», заряд миші), у галереї не пропонуються.

### Debian, Ubuntu, Mint, Pop!_OS

Завантажте `deskwidgets_<версія>_all.deb` з [Releases](../../releases):

```sh
sudo apt install ./deskwidgets_*_all.deb
```

Відкрийте **Віджети** в меню програм — перший запуск увімкне автозапуск і
<kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd>.

### Будь-який дистрибутив Linux, з вихідних кодів

Потрібні сеанс X11 або Xwayland (є в GNOME, KDE Plasma…), `python3` з `venv` і — з вашого дистрибутива — прив’язки
Python. PyGObject + GTK 4 + libadwaita ≥ 1.5 дають рідну галерею та служби «Зараз грає» й «Події»; без них
DeskWidgets використає галерею на Tk (`python3-tk`) і просто пропустить ці два віджети.

```sh
sudo apt install python3-venv python3-cairo python3-psutil python3-pil python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 \
                 gir1.2-ecal-2.0 gir1.2-edataserver-1.2 fonts-noto-cjk
git clone https://github.com/T0ks1k24/DeskWidgets
cd DeskWidgets
./install.sh            # у ~/.local; або: sudo ./install.sh --system
deskwidgets setup       # автозапуск, Super+Alt+W (GNOME), пункт меню
deskwidgets             # запуск
```

Без встановлення: `python3 -m deskwidgets` у теці проєкту. Чого бракує — покаже `deskwidgets doctor`.
Для Fedora й Arch — див. [README.md](README.md#any-linux-distribution-from-source).

**Бездротові ігрові миші** (Pulsar, Kysona, ATK, VXE … з приймачами Compx/CX) повідомляють заряд лише
власним протоколом — DeskWidgets читає його сам. При встановленні з вихідних кодів один раз потрібне
правило udev (.deb і `--system` ставлять його самі): `deskwidgets mice` покаже команду й результат.

## Як користуватися

| | |
|---|---|
| <kbd>Super</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd> або **Віджети** в меню | галерея: ваші віджети, додати нові, налаштування |
| тягнути лівою кнопкою миші | пересунути віджет; з’явиться сітка, і він стане в найближчу вільну клітинку |
| права кнопка на віджеті | його налаштування (розмір, стиль, колір, параметри, прибрати) |
| ліва кнопка | дії віджета: пауза музики, відкрити аніме на AniList… |

<kbd>Super</kbd> — це клавіша <kbd>Win</kbd> (з логотипом Windows). У Windows галерею відкриває <kbd>Ctrl</kbd>+<kbd>Alt</kbd>+<kbd>W</kbd>
і значок у треї. Пересунути віджет можна й засобами оконного менеджера (<kbd>Super</kbd>+перетягування в GNOME).

Те саме з терміналу: `deskwidgets types`, `add weather`, `list`, `configure weather-2`, `arrange`,
`preview clock --size large --style sakura -o clock.png`, `quit`.

Налаштування — у `~/.config/deskwidgets/` (`%APPDATA%\\DeskWidgets` у Windows), див. [docs/configuration.md](docs/configuration.md).

## Свій віджет

```sh
deskwidgets new pomodoro
$EDITOR ~/.config/deskwidgets/widgets/pomodoro/widget.lua
deskwidgets reload && deskwidgets add pomodoro
```

Віджет — це `widget.json` (назва, розміри, параметри — з них будується вікно налаштувань) і
`widget.lua` (малювання). Приклади — у [examples/](examples) і [deskwidgets/widgets](deskwidgets/widgets). Пул-реквести з
новими віджетами вітаються — див. [CONTRIBUTING.md](CONTRIBUTING.md).

## Ліцензія

MIT — див. [LICENSE](LICENSE). Вбудований шрифт іконок має власну ліцензію.
