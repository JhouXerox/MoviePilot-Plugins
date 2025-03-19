# MoviePilot-Plugins

这是一个MoviePilot的**第三方插件库**，提供签到相关功能扩展。

## 插件列表

### 两步验证助手 (twofahelper)
- 生成TOTP两步验证码，无需手动输入
- 配套浏览器扩展实现自动弹出验证码
- 支持多站点验证码管理
- 支持站点图标显示
- 验证码30秒自动刷新
- 支持一键复制验证码

### 飞牛论坛签到插件 (fnossign)
- 支持飞牛论坛每日自动签到
- 支持手动触发签到 
- 签到状态显示和历史记录
- 签到结果通知
- 定时签到功能
- 签到失败自动重试
- 积分信息自动获取

### 柠檬站点神游插件 (lemonshengyou)
- 支持柠檬站点每日自动神游
- 支持手动触发神游
- 自动识别用户奖励记录
- 神游结果通知
- 定时神游功能
- 神游失败自动重试
- 奖励信息自动获取

## 安装说明

**本仓库为第三方插件库，需在MoviePilot中添加仓库地址使用**

1. 在MoviePilot的插件商店页面，点击"添加第三方仓库"
2. 添加本仓库地址：`https://github.com/madrays/MoviePilot-Plugins`
3. 添加成功后，在插件列表中找到需要的插件
4. 安装并启用插件
5. 根据下方说明配置插件参数

## 使用说明

### 两步验证助手
1. 安装MP插件后，下载并安装浏览器扩展
2. 浏览器扩展用于添加和管理TOTP站点配置
3. MP插件负责生成验证码，浏览器扩展负责显示和填写
4. 无需手动输入API或其他复杂配置
5. 详细说明请查看[TOTP浏览器扩展](#totp-browser-extension)

### 飞牛论坛签到插件
1. 获取Cookie：登录飞牛论坛后，按F12打开开发者工具，在网络或应用程序选项卡中复制Cookie
2. 在插件设置中填入Cookie
3. 设置签到时间（推荐早上8点，cron表达式：`0 8 * * *`）
4. 启用插件并保存

### 柠檬站点神游插件
1. 确保已在MoviePilot中添加并配置好柠檬站点
2. 在插件设置中选择要进行神游的柠檬站点
3. 设置神游时间（推荐早上8点，cron表达式：`0 8 * * *`）
4. 可选择开启通知，在神游后收到结果通知
5. 启用插件并保存

## TOTP浏览器扩展

TOTP两步验证助手需要配合浏览器扩展使用：

1. 下载浏览器扩展：[下载链接](https://github.com/madrays/MoviePilot-Plugins/raw/main/TOTP-Extension.zip)
2. 解压下载的文件
3. 在浏览器的扩展管理页面中选择"加载已解压的扩展程序"
4. 选择解压后的文件夹
5. 扩展安装完成后，点击浏览器扩展图标进入设置
6. 添加站点和密钥信息
7. 浏览扩展会自动从MoviePilot获取验证码并在需要时弹出

## 许可证

本项目采用 MIT 许可证 - 详见 [LICENSE](LICENSE) 文件 