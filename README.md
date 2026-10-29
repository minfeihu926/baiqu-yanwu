# 百趣演武 · 自动抽牌

在《明日方舟：终末地》「百趣演武」抽牌界面自动逐张抽牌:

- 抽到第 `n` 张时,若点数 5 的张数 `< n-1`(剩余位置全出 5 也凑不够 4 张)→ **放弃挑战 → 确认 → 重抽**;
- 抽满 5 张且 **至少 4 张点数 5** → 停手,手牌保留,脚本退出;
- 抽满 5 张仍不足 4 张 → 放弃重抽。

脚本**不点「开始挑战」**,不参与战斗,只负责把牌抽到达标。

> 非官方工具,与鹰角网络无关。使用风险自负。

## 下载即用(exe)

到 [Releases](../../releases) 下载:

- `baiqu_yanyu.exe`:单文件版,**不需要安装 Python 或任何依赖**,双击即可运行;
- `baiqu_yanyu_win64_portable.zip`:便携版,解压后运行 `baiqu_yanyu.exe`,单文件版被杀软误报时可用。

```bat
baiqu_yanyu.exe --check
baiqu_yanyu.exe
```

单文件版首次启动会把自身解压到系统临时目录,会慢 1~2 秒;如果 Windows Defender 误报,
加白名单或改用便携版即可。

## 特性

- 纯本地运行,不联网、不上传截图
- 逐张判定,提前放弃,不做无意义的抽牌
- 点数识别用模板匹配(不依赖 OCR 服务)
- 卡槽按卡牌几何比例定位,1280×720 / 1280×800 等窗口均可
- 点击前校验游戏窗口是否在前台,避免误点其它程序
- 卡面渲染完成(点数出现)后才计入,避免翻牌动画中途误判

## 环境

- Windows 10 / 11
- 游戏窗口化运行,不要最小化;运行时脚本会独占鼠标
- 二选一:
  - 直接用 Releases 里的 `baiqu_yanyu.exe`(无需 Python)
  - 从源码运行:Python 3.9+,并安装依赖:

```bat
python -m pip install numpy pillow
```

## 快速开始

1. 进入百趣演武的抽牌界面(5 张牌背面朝上);
2. 先检查识别(`baiqu_yanyu.exe` 换成 `python baiqu_yanyu.py` 即为源码版,下同):

```bat
baiqu_yanyu.exe --check
```

   正常输出类似:

   ```
   识别到卡槽: 5
     #1 翻开=False 是5=False 分数=0.000
     ...
   已翻开: 0  点数5: 0
   按钮 draw: (0.928, 0.784)
   按钮 abandon: 未找到
   ```

3. 正式运行:

```bat
baiqu_yanyu.exe
```

   源码版也可以双击 `run.bat`(自动补依赖,结束暂停)。

4. 抽满 5 张且 ≥4 张 5 后脚本退出,手牌留给你,手动点「开始挑战」。
   `Ctrl+C` 可随时中断。

## 参数

| 参数 | 说明 |
|---|---|
| `--check` | 只截图识别,不点击 |
| `--rounds N` | 最多放弃重抽多少轮,默认 60 |
| `--hwnd 0x...` | 手动指定游戏窗口句柄 |
| `--image PATH` | 离线分析一张截图 |
| `--version` | 显示版本 |

## 工作原理

1. **找卡槽**:青色卡背颜色做列投影,得到 5 个等距卡槽;翻开后靠等间距推算。
2. **判断已翻开**:卡槽左上角同时满足「白底 > 25%」和「深色文字 > 5%」,避免把翻牌动画中的空白卡面当成 0 点。
3. **判断点数 5**:`templates/digit5.png` 在点数区域做多尺度归一化互相关,阈值 0.75。
4. **找按钮**:按颜色定位——抽牌=青色、放弃挑战=红色、确认=黄色。
5. **决策**:已抽 `n` 张、其中 5 有 `f` 张:
   - `n=5, f≥4` → 停手
   - `n=5, f<4` → 放弃
   - `n<5, f<n-1` → 放弃
   - 否则继续抽

同一轮内牌数只增不减,已确认的 5 也不会因为某一帧漏识别而回退。

## 概率与耗时

按牌库 `6/6/6/6/10` 计算:

- 5 张里 **≥4 张 5**:约 **1.9%**,期望约 53 轮;
- 单轮约 2~3 秒,平均 **2~3 分钟**出结果(运气差会更久)。

如果目标只是拿满奖励(战力点 20 / 150000),`4,4,4,4,4`、`5,5,5,4,1` 等组合同样满足,
概率约 **14.6%**,可直接改判定条件。

## 排错

- **找不到游戏窗口**:运行 `python tools/probe_windows.py`,把输出发到 issue。
  脚本会按「类名 UnityWndClass + 标题含 Endfield」→「进程名 Endfield*.exe」→「Unity 窗口」匹配,
  最小化的窗口会自动还原。
- **找不到抽牌按钮**:确认游戏在抽牌界面、窗口完整在屏幕内、语言/滤镜没改变 UI 配色;发 `--check` 输出。
- **点击没反应**:脚本和游戏权限保持一致(游戏是管理员就用管理员运行脚本);
  确认游戏在前台,`--check` 能识别到 `draw` 按钮。
- **跑很久**:4 张 5 本身就是小概率事件,属于预期;嫌慢可改用战力点判定。

## 已知限制

- 只处理第一轮,不点开始挑战;
- 依赖当前游戏 UI 的布局与配色,大版本更新后可能需要重新校准 `BTN_SPECS` / 模板;
- 仅支持 Windows。

## 自行打包 exe

```bat
build.bat
```

或手动:

```bat
python -m pip install -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean baiqu_yanyu.spec
```

产物为 `dist/baiqu_yanyu.exe`(单文件、自带 Python 与依赖)。
GitHub Actions 也会在推送 `v*` tag 时自动构建并上传到 Release。

## 许可证

[MIT](LICENSE)

---

### English

A small Windows tool for the "百趣演武" card-drawing screen in *Arknights: Endfield*.
It draws cards one by one, abandons the hand as soon as four 5-point cards become impossible,
and stops once five cards contain at least four 5s. It never clicks "start challenge" and never
touches the mouse outside the game window.

A standalone `baiqu_yanyu.exe` (no Python required) is available in Releases; building from
source needs Python 3.9+, `numpy` and `pillow`.
