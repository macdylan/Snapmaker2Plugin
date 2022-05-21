[Please click here for English README.md](README.en-us.md)
# Snapmaker 2 Plugin for Cura
- 无需配置，通过 UDP 广播自动查找局域网内所有 Snapmaker 2 设备
- 无需保存文件，使用网络直接发送到打印机进行作业
- 提供了 Snapmaker 2 兼容的 GCode 格式：显示缩略图、快速解析打印参数

# Installation
## 打印机配置
Cura 4.1 以后已经内置了 Snapmaker 2 的配置文件，参考 https://support.snapmaker.com/hc/en-us/articles/360044341034

## 从 Cura Marketplace 安装
1. 点击 Cura 主界面右上角的 市场 按钮
2. 点击右边图标，浏览器进入 网上市场，在左侧搜索框检索 Snapmaker2Plugin
3. 点击安装，确认协议条款等待安装完成
4. 重启 Cura

## 从 Github 安装
1. 从 Cura 的 Help 菜单下打开 Show Configuration Folder，进入 plugins 文件夹
2. 推荐使用 `git clone https://github.com/macdylan/Snapmaker2Plugin.git` 进行安装，或
3. 下载 [release](https://github.com/macdylan/Snapmaker2Plugin/releases) 的 zip 包，解压到该文件夹
4. 重启 Cura

# Usage
- 对模型切片后，将在 Save to File 按钮的位置出现设备选择菜单

  ![](_snapshots/sendto.png)

- 选择需要发送的设备，点击 Send to
- 从触摸屏进行连接授权
- 等待文件发送完成

  ![](_snapshots/screen_auth.png)

- 在 Snapmaker 2 触摸屏确认进行打印

  ![](_snapshots/preview.jpg)

你也可以使用 Save to File，将文件保存为 Snapmaker G-code file(*.gcode) 格式

  ![](_snapshots/savetofile.png)

以上说明适用于固件 1.12 后续版本，不同版本的界面可能有所调整。

# 问题排查
⚠️ Snapmaker 2 的无线连接有时候不稳定，如果无法出现设备选择菜单，可按如下方法排查：

    1. 检查 Snapmaker 2 是否已经联网
    2. 检查电脑的防火墙，是否阻止了 Cura 访问局域网（win10 默认会阻止）
    3. 等待 5-10 秒，Cura 会持续查找局域网内所有兼容设备并自动显示
    4. 重启 Snapmaker 2 并等待联网，因为它的应答服务可能挂了
    5. 检查路由器设置，是否阻止了 UDP 广播
    6. 如果可能，确保电脑、Snapmaker 2、路由器尽可能靠近，避免丢包率过高

如仍无法解决，请提供 cura.log 文件到 issues 中以便分析，感谢你的帮助。


---
**__Make Something Wonderful__**
