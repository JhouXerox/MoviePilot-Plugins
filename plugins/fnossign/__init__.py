"""
飞牛论坛签到插件
版本: 2.0
作者: madrays
功能:
- 自动完成飞牛论坛每日签到
- 支持签到失败重试
- 保存签到历史记录
- 提供详细的签到通知
- 增强的错误处理和日志

修改记录:
- v2.0: 初始版本，基本签到功能
"""
import time
import requests
import re
from datetime import datetime, timedelta

import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import settings
from app.plugins import _PluginBase
from typing import Any, List, Dict, Tuple, Optional
from app.log import logger
from app.schemas import NotificationType


class fnossign(_PluginBase):
    # 插件名称
    plugin_name = "飞牛论坛签到"
    # 插件描述
    plugin_desc = "自动完成飞牛论坛每日签到，支持失败重试和历史记录"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/madrays/MoviePilot-Plugins/main/icons/fnos.ico"
    # 插件版本
    plugin_version = "2.0"
    # 插件作者
    plugin_author = "madrays"
    # 作者主页
    author_url = "https://github.com/madrays"
    # 插件配置项ID前缀
    plugin_config_prefix = "fnossign_"
    # 加载顺序
    plugin_order = 1
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    _cookie = None
    _notify = False
    _onlyonce = False
    _cron = None
    _scheduler = None
    _max_retries = 3  # 最大重试次数
    _retry_interval = 30  # 重试间隔(秒)
    _history_days = 30  # 历史保留天数
    _manual_trigger = False

    def init_plugin(self, config: dict = None):
        logger.info("============= fnossign 初始化 =============")
        try:
            if config:
                self._enabled = config.get("enabled")
                self._cookie = config.get("cookie")
                self._notify = config.get("notify")
                self._cron = config.get("cron")
                self._onlyonce = config.get("onlyonce")
                self._max_retries = int(config.get("max_retries", 3))
                self._retry_interval = int(config.get("retry_interval", 30))
                self._history_days = int(config.get("history_days", 30))
                logger.info(f"配置: enabled={self._enabled}, notify={self._notify}, cron={self._cron}, max_retries={self._max_retries}, retry_interval={self._retry_interval}, history_days={self._history_days}")
            
            if self._onlyonce:
                logger.info("执行一次性签到")
                self._onlyonce = False
                self._manual_trigger = True  # 标记为手动触发
                self.update_config({
                    "onlyonce": False,
                    "enabled": self._enabled,
                    "cookie": self._cookie,
                    "notify": self._notify,
                    "cron": self._cron,
                    "max_retries": self._max_retries,
                    "retry_interval": self._retry_interval,
                    "history_days": self._history_days
                })
                self.sign()
        except Exception as e:
            logger.error(f"fnossign初始化错误: {str(e)}", exc_info=True)

    def sign(self, retry_count=0):
        """执行签到，支持失败重试"""
        logger.info("============= 开始签到 =============")
        try:
            # 检查是否今日已成功签到（通过记录）
            if not self._is_manual_trigger() and self._is_already_signed_today():
                logger.info("根据历史记录，今日已成功签到，跳过本次执行")
                return {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "跳过: 今日已签到",
                }
            
            # 检查先决条件
            if not self._cookie:
                logger.error("签到失败：未配置Cookie")
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败: 未配置Cookie",
                }
                self._save_sign_history(sign_dict)
                
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage, 
                        title="【飞牛论坛签到失败】",
                        text="❌ 未配置Cookie，请在插件设置中添加Cookie"
                    )
                return sign_dict
            
            logger.info(f"使用Cookie长度: {len(self._cookie)} 字符")
            
            # 从完整Cookie中提取关键值
            cookies = self._extract_required_cookies(self._cookie)
            if not cookies or 'pvRK_2132_saltkey' not in cookies or 'pvRK_2132_auth' not in cookies:
                logger.error("签到失败：Cookie中缺少必要的认证信息")
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败: Cookie中缺少必要的认证信息",
                }
                self._save_sign_history(sign_dict)
                
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage, 
                        title="【飞牛论坛签到失败】",
                        text="❌ Cookie中缺少必要的认证信息，请更新Cookie"
                    )
                return sign_dict
            
            # 设置请求头和会话
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.6261.95 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
                "Accept-Language": "zh-CN,zh;q=0.9",
                "Connection": "keep-alive",
                "Referer": "https://club.fnnas.com/",
                "DNT": "1"
            }
            
            # 创建session并添加重试机制
            session = requests.Session()
            session.headers.update(headers)
            session.cookies.update(cookies)
            
            # 添加重试机制
            retry = requests.adapters.Retry(
                total=3,
                backoff_factor=1,
                status_forcelist=[500, 502, 503, 504]
            )
            adapter = requests.adapters.HTTPAdapter(max_retries=retry)
            session.mount('http://', adapter)
            session.mount('https://', adapter)
            
            # 验证Cookie是否有效
            if not self._check_cookie_valid(session):
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败: Cookie无效或已过期",
                }
                self._save_sign_history(sign_dict)
                
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【飞牛论坛签到失败】",
                        text="❌ Cookie无效或已过期，请更新Cookie"
                    )
                return sign_dict
            
            # 步骤1: 访问签到页面获取sign参数
            logger.info("正在访问论坛首页...")
            session.get("https://club.fnnas.com/")
            
            logger.info("正在访问签到页面...")
            sign_page_url = "https://club.fnnas.com/plugin.php?id=zqlj_sign"
            response = session.get(sign_page_url)
            html_content = response.text
            
            # 检查是否已经签到
            if "您今天已经打过卡了" in html_content:
                logger.info("今日已签到")
                sign_dict = self._get_credit_info_and_create_record(session, "已签到")
                
                # 发送通知
                if self._notify:
                    self._send_sign_notification(sign_dict)
                
                return sign_dict
            
            # 从页面中提取sign参数
            sign_match = re.search(r'sign&sign=(.+)" class="btna', html_content)
            if not sign_match:
                logger.error("未找到签到参数")
                
                # 尝试重试
                if retry_count < self._max_retries:
                    logger.info(f"将在{self._retry_interval}秒后进行第{retry_count+1}次重试...")
                    time.sleep(self._retry_interval)
                    return self.sign(retry_count + 1)
                
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败: 未找到签到参数",
                }
                self._save_sign_history(sign_dict)
                
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【飞牛论坛签到失败】",
                        text="❌ 签到失败: 未找到签到参数"
                    )
                return sign_dict
            
            sign_param = sign_match.group(1)
            logger.info(f"找到签到按钮 (匹配规则: '签到')")
            
            # 步骤2: 使用提取的sign参数执行签到
            logger.info("正在执行签到...")
            sign_url = f"https://club.fnnas.com/plugin.php?id=zqlj_sign&sign={sign_param}"
            
            # 更新Referer头
            session.headers.update({"Referer": sign_page_url})
            
            response = session.get(sign_url)
            html_content = response.text
            
            # 储存响应以便调试
            debug_resp = html_content[:500]
            logger.info(f"签到响应内容预览: {debug_resp}")
            
            # 检查签到结果
            if "恭喜您，打卡成功" in html_content or "打卡成功" in html_content:
                logger.info("签到成功")
                sign_dict = self._get_credit_info_and_create_record(session, "签到成功")
                
                # 发送通知
                if self._notify:
                    self._send_sign_notification(sign_dict)
                
                return sign_dict
            elif "您今天已经打过卡了" in html_content:
                logger.info("今日已签到")
                sign_dict = self._get_credit_info_and_create_record(session, "已签到")
                
                # 发送通知
                if self._notify:
                    self._send_sign_notification(sign_dict)
                
                return sign_dict
            else:
                # 签到可能失败
                logger.error(f"签到请求发送成功，但结果异常: {debug_resp}")
                
                # 尝试重试
                if retry_count < self._max_retries:
                    logger.info(f"将在{self._retry_interval}秒后进行第{retry_count+1}次重试...")
                    time.sleep(self._retry_interval)
                    return self.sign(retry_count + 1)
                
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": "签到失败: 响应内容异常",
                }
                self._save_sign_history(sign_dict)
                
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【飞牛论坛签到失败】",
                        text="❌ 签到失败: 响应内容异常"
                    )
                return sign_dict
        
        except requests.RequestException as req_exc:
            # 网络请求异常处理
            logger.error(f"网络请求异常: {str(req_exc)}")
            if retry_count < self._max_retries:
                logger.info(f"将在{self._retry_interval}秒后进行第{retry_count+1}次重试...")
                time.sleep(self._retry_interval)
                return self.sign(retry_count + 1)
            else:
                # 记录失败
                sign_dict = {
                    "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                    "status": f"签到失败: 网络请求异常 - {str(req_exc)}",
                }
                self._save_sign_history(sign_dict)
                
                # 发送失败通知
                if self._notify:
                    self.post_message(
                        mtype=NotificationType.SiteMessage,
                        title="【飞牛论坛签到失败】",
                        text=f"❌ 网络请求异常: {str(req_exc)}"
                    )
                
                return sign_dict
                
        except Exception as e:
            # 签到过程中的异常
            logger.error(f"签到过程异常: {str(e)}", exc_info=True)
            
            # 记录失败
            sign_dict = {
                "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
                "status": f"签到失败: {str(e)}",
            }
            self._save_sign_history(sign_dict)
            
            # 发送失败通知
            if self._notify:
                self.post_message(
                    mtype=NotificationType.SiteMessage,
                    title="【飞牛论坛签到失败】",
                    text=f"❌ 签到过程发生异常: {str(e)}"
                )
                
            return sign_dict
            
    def _get_credit_info_and_create_record(self, session, status):
        """获取积分信息并创建签到记录"""
        # 步骤3: 获取积分信息
        credit_info = self._get_credit_info(session)
        
        # 创建签到记录
        sign_dict = {
            "date": datetime.today().strftime('%Y-%m-%d %H:%M:%S'),
            "status": status,
            "fnb": credit_info.get("fnb", 0),
            "nz": credit_info.get("nz", 0),
            "credit": credit_info.get("jf", 0),
            "login_days": credit_info.get("login_days", 0)
        }
        
        # 保存签到记录
        self._save_sign_history(sign_dict)
        
        # 记录最后一次成功签到的日期
        if "签到成功" in status or "已签到" in status:
            self._save_last_sign_date()
        
        return sign_dict

    def _get_credit_info(self, session):
        """
        获取积分信息并解析
        """
        try:
            # 访问正确的积分页面
            credit_url = "https://club.fnnas.com/home.php?mod=spacecp&ac=credit&showcredit=1"
            response = session.get(credit_url)
            response.raise_for_status()
            
            # 检查是否重定向到登录页
            if "您需要先登录才能继续本操作" in response.text or "请先登录后才能继续浏览" in response.text:
                logger.error("获取积分信息失败：需要登录")
                return {}  # 返回空字典，表示获取失败
            
            html_content = response.text
            
            # 创建积分信息字典
            credit_info = {}
            
            # 基于实际HTML结构创建精确的匹配模式
            # 首先尝试提取整个积分区块
            credit_block_pattern = r'<ul class="creditl mtm bbda cl">.*?</ul>'
            credit_block_match = re.search(credit_block_pattern, html_content, re.DOTALL)
            
            if credit_block_match:
                credit_block = credit_block_match.group(0)
                logger.info("成功找到积分信息区块")
                
                # 从区块中提取各项积分
                # 飞牛币
                fnb_pattern = r'<em>\s*飞牛币:\s*</em>(\d+)'
                fnb_match = re.search(fnb_pattern, credit_block)
                if fnb_match:
                    credit_info["fnb"] = int(fnb_match.group(1))
                    logger.info(f"成功提取飞牛币: {credit_info['fnb']}")
                
                # 牛值
                nz_pattern = r'<em>\s*牛值:\s*</em>(\d+)'
                nz_match = re.search(nz_pattern, credit_block)
                if nz_match:
                    credit_info["nz"] = int(nz_match.group(1))
                    logger.info(f"成功提取牛值: {credit_info['nz']}")
                
                # 登陆天数
                ts_pattern = r'<em>\s*登陆天数:\s*</em>(\d+)'
                ts_match = re.search(ts_pattern, credit_block)
                if ts_match:
                    credit_info["ts"] = int(ts_match.group(1))
                    logger.info(f"成功提取登陆天数: {credit_info['ts']}")
                
                # 积分
                jf_pattern = r'<em>\s*积分:\s*</em>(\d+)'
                jf_match = re.search(jf_pattern, credit_block)
                if jf_match:
                    credit_info["jf"] = int(jf_match.group(1))
                    logger.info(f"成功提取积分: {credit_info['jf']}")
            else:
                logger.warning("未找到积分信息区块，尝试使用备用方法")
                
                # 备用方法：直接在整个页面中搜索
                # 飞牛币
                fnb_patterns = [
                    r'<em>\s*飞牛币:\s*</em>(\d+)',
                    r'飞牛币:\s*(\d+)',
                    r'飞牛币</em>\s*(\d+)'
                ]
                
                for pattern in fnb_patterns:
                    fnb_match = re.search(pattern, html_content, re.DOTALL)
                    if fnb_match:
                        credit_info["fnb"] = int(fnb_match.group(1))
                        logger.info(f"通过备用方法找到飞牛币: {credit_info['fnb']}")
                        break
                
                # 牛值
                nz_patterns = [
                    r'<em>\s*牛值:\s*</em>(\d+)',
                    r'牛值:\s*(\d+)',
                    r'牛值</em>\s*(\d+)'
                ]
                
                for pattern in nz_patterns:
                    nz_match = re.search(pattern, html_content, re.DOTALL)
                    if nz_match:
                        credit_info["nz"] = int(nz_match.group(1))
                        logger.info(f"通过备用方法找到牛值: {credit_info['nz']}")
                        break
                
                # 登陆天数
                ts_patterns = [
                    r'<em>\s*登陆天数:\s*</em>(\d+)',
                    r'登陆天数:\s*(\d+)',
                    r'登陆天数</em>\s*(\d+)'
                ]
                
                for pattern in ts_patterns:
                    ts_match = re.search(pattern, html_content, re.DOTALL)
                    if ts_match:
                        credit_info["ts"] = int(ts_match.group(1))
                        logger.info(f"通过备用方法找到登陆天数: {credit_info['ts']}")
                        break
                
                # 积分
                jf_patterns = [
                    r'<em>\s*积分:\s*</em>(\d+)',
                    r'积分:\s*(\d+)',
                    r'积分</em>\s*(\d+)'
                ]
                
                for pattern in jf_patterns:
                    jf_match = re.search(pattern, html_content, re.DOTALL)
                    if jf_match:
                        credit_info["jf"] = int(jf_match.group(1))
                        logger.info(f"通过备用方法找到积分: {credit_info['jf']}")
                        break
            
            # 检查是否成功提取了所有积分信息
            required_fields = ["fnb", "nz", "ts", "jf"]
            missing_fields = [field for field in required_fields if field not in credit_info]
            
            if missing_fields:
                logger.error(f"积分信息提取不完整，缺少以下字段: {', '.join(missing_fields)}")
                
                # 不返回默认值，而是返回已成功提取的值，缺失的值保持为空
                return credit_info
            
            logger.info(f"成功获取所有积分信息: 飞牛币={credit_info.get('fnb')}, 牛值={credit_info.get('nz')}, "
                      f"积分={credit_info.get('jf')}, 登录天数={credit_info.get('ts')}")
            
            return credit_info
            
        except requests.RequestException as request_exception:
            logger.error(f"获取积分信息网络错误: {str(request_exception)}")
            return {}  # 返回空字典，表示获取失败
            
        except Exception as e:
            logger.error(f"获取积分信息失败: {str(e)}", exc_info=True)
            return {}  # 返回空字典，表示获取失败

    def _save_sign_history(self, sign_data):
        """
        保存签到历史记录
        """
        try:
            # 读取现有历史
            history = self.get_data('sign_history') or []
            
            # 确保日期格式正确
            if "date" not in sign_data:
                sign_data["date"] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                
            history.append(sign_data)
            
            # 清理旧记录
            retention_days = int(self._history_days)
            now = datetime.now()
            valid_history = []
            
            for record in history:
                try:
                    # 尝试将记录日期转换为datetime对象
                    record_date = datetime.strptime(record["date"], '%Y-%m-%d %H:%M:%S')
                    # 检查是否在保留期内
                    if (now - record_date).days < retention_days:
                        valid_history.append(record)
                except (ValueError, KeyError):
                    # 如果记录日期格式不正确，尝试修复
                    logger.warning(f"历史记录日期格式无效: {record.get('date', '无日期')}")
                    # 添加新的日期并保留记录
                    record["date"] = datetime.today().strftime('%Y-%m-%d %H:%M:%S')
                    valid_history.append(record)
            
            # 保存历史
            self.save_data(key="sign_history", value=valid_history)
            logger.info(f"保存签到历史记录，当前共有 {len(valid_history)} 条记录")
            
        except Exception as e:
            logger.error(f"保存签到历史记录失败: {str(e)}", exc_info=True)

    def _send_sign_notification(self, sign_dict):
        """
        发送签到通知
        """
        if not self._notify:
            return
            
        status = sign_dict.get("status", "未知")
        fnb = sign_dict.get("fnb", "—")
        nz = sign_dict.get("nz", "—")
        credit = sign_dict.get("credit", "—")
        login_days = sign_dict.get("login_days", "—")
        
        # 检查积分信息是否为空
        credits_missing = fnb == "—" and nz == "—" and credit == "—" and login_days == "—"
        
        # 构建通知文本
        if "签到成功" in status or "已签到" in status:
            title = "【飞牛论坛签到成功】"
            
            if credits_missing:
                text = f"✅ 状态: {status}\n\n" \
                       f"⚠️ 积分信息获取失败，请手动登录网站查看"
            else:
                text = f"✅ 状态: {status}\n" \
                       f"💎 飞牛币: {fnb}\n" \
                       f"🔥 牛值: {nz}\n" \
                       f"✨ 积分: {credit}\n" \
                       f"📆 登录天数: {login_days}"
        else:
            title = "【飞牛论坛签到失败】"
            text = f"❌ 状态: {status}\n\n" \
                   f"⚠️ 可能的解决方法:\n" \
                   f"• 检查Cookie是否过期\n" \
                   f"• 确认站点是否可正常访问\n" \
                   f"• 手动登录查看是否需要验证码"
            
        # 发送通知
        self.post_message(
            mtype=NotificationType.SiteMessage,
            title=title,
            text=text
        )

    def get_state(self) -> bool:
        logger.info(f"fnossign状态: {self._enabled}")
        return self._enabled

    def get_service(self) -> List[Dict[str, Any]]:
        if self._enabled and self._cron:
            logger.info(f"注册定时服务: {self._cron}")
            return [{
                "id": "fnossign",
                "name": "飞牛论坛签到",
                "trigger": CronTrigger.from_crontab(self._cron),
                "func": self.sign,
                "kwargs": {}
            }]
        return []

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        return [
            {
                'component': 'VForm',
                'content': [
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-h6'
                                },
                                'text': '🔄 基本设置'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'enabled',
                                                            'label': '启用插件',
                                                            'color': 'primary',
                                                            'hide-details': False,
                                                            'density': 'comfortable',
                                                            'prepend-icon': 'mdi-power'
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'notify',
                                                            'label': '开启通知',
                                                            'color': 'info',
                                                            'hide-details': False,
                                                            'density': 'comfortable',
                                                            'prepend-icon': 'mdi-bell'
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 4
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VSwitch',
                                                        'props': {
                                                            'model': 'onlyonce',
                                                            'label': '立即运行一次',
                                                            'color': 'success',
                                                            'hide-details': False,
                                                            'density': 'comfortable',
                                                            'prepend-icon': 'mdi-play'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-h6'
                                },
                                'text': '🔐 账号设置'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'cookie',
                                                            'label': '站点Cookie',
                                                            'placeholder': '请输入站点Cookie值',
                                                            'variant': 'outlined',
                                                            'prepend-icon': 'mdi-cookie',
                                                            'hide-details': False,
                                                            'persistent-hint': True,
                                                            'hint': '复制浏览器中的完整Cookie字符串，必须包含pvRK_2132_saltkey和pvRK_2132_auth'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-h6'
                                },
                                'text': '⚙️ 高级设置'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'cron',
                                                            'label': '签到周期',
                                                            'placeholder': '0 8 * * *',
                                                            'prepend-icon': 'mdi-calendar-clock',
                                                            'hint': '使用cron表达式设置执行时间',
                                                            'persistent-hint': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'max_retries',
                                                            'label': '最大重试次数',
                                                            'type': 'number',
                                                            'placeholder': '3',
                                                            'prepend-icon': 'mdi-refresh',
                                                            'hint': '签到失败后的自动重试次数',
                                                            'persistent-hint': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'retry_interval',
                                                            'label': '重试间隔(秒)',
                                                            'type': 'number',
                                                            'placeholder': '30',
                                                            'prepend-icon': 'mdi-timer',
                                                            'hint': '每次重试之间的等待时间',
                                                            'persistent-hint': True
                                                        }
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12,
                                                    'md': 3
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VTextField',
                                                        'props': {
                                                            'model': 'history_days',
                                                            'label': '历史保留天数',
                                                            'type': 'number',
                                                            'placeholder': '30',
                                                            'prepend-icon': 'mdi-history',
                                                            'hint': '签到历史记录的保留时间',
                                                            'persistent-hint': True
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCard',
                        'props': {
                            'variant': 'outlined',
                            'class': 'mb-4'
                        },
                        'content': [
                            {
                                'component': 'VCardTitle',
                                'props': {
                                    'class': 'text-h6'
                                },
                                'text': '📚 使用教程'
                            },
                            {
                                'component': 'VCardText',
                                'content': [
                                    {
                                        'component': 'VRow',
                                        'content': [
                                            {
                                                'component': 'VCol',
                                                'props': {
                                                    'cols': 12
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VExpansionPanels',
                                                        'props': {
                                                            'variant': 'accordion',
                                                            'elevation': 0
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VExpansionPanel',
                                                                'props': {
                                                                    'title': '1️⃣ 如何获取飞牛论坛Cookie?',
                                                                    'text': '- 使用Chrome浏览器访问飞牛论坛官网并登录\n- 按F12打开开发者工具，选择"应用程序"或"Application"选项卡\n- 在左侧找到"Cookies"，点击"https://club.fnnas.com"\n- 右键点击空白处，选择"复制全部"或"Copy all as string"\n- 粘贴到插件的Cookie输入框中即可'
                                                                }
                                                            },
                                                            {
                                                                'component': 'VExpansionPanel',
                                                                'props': {
                                                                    'title': '2️⃣ 如何设置签到时间?',
                                                                    'text': '签到周期使用标准cron表达式设置，默认值"0 8 * * *"表示每天早上8点执行。\n\n常用设置示例：\n- 每天早上7点: 0 7 * * *\n- 每天中午12点: 0 12 * * *\n- 每小时执行一次: 0 * * * *\n- 每天0点和12点执行: 0 0,12 * * *'
                                                                }
                                                            },
                                                            {
                                                                'component': 'VExpansionPanel',
                                                                'props': {
                                                                    'title': '3️⃣ 如何判断签到是否成功?',
                                                                    'text': '- 启用"开启通知"选项后，每次签到都会发送通知\n- 通知内容包括签到状态和积分信息\n- 在插件页面可以查看历史签到记录和详细数据\n- 绿色状态表示签到成功，红色表示失败'
                                                                }
                                                            },
                                                            {
                                                                'component': 'VExpansionPanel',
                                                                'props': {
                                                                    'title': '4️⃣ 签到失败怎么办?',
                                                                    'text': '常见签到失败原因及解决方法：\n\n- Cookie无效或过期：重新获取最新的Cookie并更新\n- 网络连接问题：检查网络连接，或增加重试次数\n- 论坛改版或维护：等待插件更新或论坛恢复正常\n- 插件配置问题：检查Cookie格式是否正确完整'
                                                                }
                                                            },
                                                            {
                                                                'component': 'VExpansionPanel',
                                                                'props': {
                                                                    'title': '5️⃣ 高级功能说明',
                                                                    'text': '- 最大重试次数：签到失败后自动重试的次数，建议设置为3-5\n- 重试间隔：每次重试之间的等待时间，单位为秒\n- 立即运行一次：不受签到周期限制，立即执行一次签到\n- 历史保留天数：签到记录的保存时间，避免数据过多占用空间'
                                                                }
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            },
                            {
                                'component': 'VCardActions',
                                'content': [
                                    {
                                        'component': 'VSpacer'
                                    },
                                    {
                                        'component': 'VBtn',
                                        'props': {
                                            'color': 'primary',
                                            'variant': 'tonal',
                                            'prepend-icon': 'mdi-github'
                                        },
                                        'text': '插件源码',
                                        'events': {
                                            'click': {
                                                'action': 'link',
                                                'link': 'https://github.com/madrays/MoviePilot-Plugins'
                                            }
                                        }
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ], {
            "enabled": False,
            "notify": True,
            "onlyonce": False,
            "cookie": "",
            "cron": "0 8 * * *",
            "max_retries": 3,
            "retry_interval": 30,
            "history_days": 30
        }

    def get_page(self) -> List[dict]:
        history = self.get_data('sign_history') or []
        run_count = len(history)
        success_count = sum(1 for record in history if "签到成功" in record.get("status", "") or "已签到" in record.get("status", ""))
        fail_count = run_count - success_count
        
        # 计算成功率
        success_rate = 0
        if run_count > 0:
            success_rate = round((success_count / run_count) * 100, 2)
        
        # 获取今日是否已签到
        today = datetime.now().strftime('%Y-%m-%d')
        today_signed = False
        today_record = None
        
        for record in history:
            record_date = record.get("date", "").split(' ')[0]
            if record_date == today and ("签到成功" in record.get("status", "") or "已签到" in record.get("status", "")):
                today_signed = True
                today_record = record
                break
        
        # 计算总积分
        total_fnb = 0
        for record in history:
            if "签到成功" in record.get("status", "") or "已签到" in record.get("status", ""):
                total_fnb += record.get("fnb", 0)
        
        return [
            {
                'component': 'VRow',
                'content': [
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12
                        },
                        'content': [
                            {
                                'component': 'VAlert',
                                'props': {
                                    'type': 'info',
                                    'variant': 'tonal',
                                    'icon': 'mdi-information',
                                    'text': f'签到插件状态：{"✅ 已启用" if self._enabled else "❌ 未启用"}   |   执行周期：{self._cron or "未设置"}   |   最近{run_count}次签到中成功{success_count}次，成功率{success_rate}%',
                                    'class': 'mb-4'
                                }
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'outlined',
                                    'class': 'mb-4',
                                    'color': 'success' if today_signed else 'warning'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'text-h6'
                                        },
                                        'text': '今日签到状态'
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'props': {
                                                    'align': 'center',
                                                    'justify': 'center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'div',
                                                        'props': {
                                                            'class': 'text-center'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'VIcon',
                                                                'props': {
                                                                    'size': 'x-large',
                                                                    'color': 'success' if today_signed else 'warning'
                                                                },
                                                                'text': 'mdi-check-circle' if today_signed else 'mdi-alert'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-h6 mt-2'
                                                                },
                                                                'text': '已完成签到' if today_signed else '尚未签到'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-subtitle-2 mt-1'
                                                                },
                                                                'text': f'签到时间: {today_record.get("date", "").split(" ")[1]}' if today_signed and today_record else f'计划时间: {self._cron.replace("0 ", "") if self._cron else "未设置"}'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'outlined',
                                    'class': 'mb-4',
                                    'color': 'primary'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'text-h6'
                                        },
                                        'text': '签到统计'
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'props': {
                                                    'align': 'center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 6,
                                                            'class': 'text-center'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-h4 font-weight-bold'
                                                                },
                                                                'text': f'{success_count}'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-caption'
                                                                },
                                                                'text': '成功次数'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'VCol',
                                                        'props': {
                                                            'cols': 6,
                                                            'class': 'text-center'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-h4 font-weight-bold'
                                                                },
                                                                'text': f'{fail_count}'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-caption'
                                                                },
                                                                'text': '失败次数'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            },
                                            {
                                                'component': 'div',
                                                'props': {
                                                    'class': 'mt-3 text-center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VProgressLinear',
                                                        'props': {
                                                            'model-value': success_rate,
                                                            'color': 'success',
                                                            'height': '12',
                                                            'striped': True
                                                        }
                                                    },
                                                    {
                                                        'component': 'div',
                                                        'props': {
                                                            'class': 'text-caption mt-1'
                                                        },
                                                        'text': f'成功率 {success_rate}%'
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'outlined',
                                    'class': 'mb-4',
                                    'color': 'info'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'text-h6'
                                        },
                                        'text': '积分情况'
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VRow',
                                                'props': {
                                                    'align': 'center',
                                                    'justify': 'center'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'div',
                                                        'props': {
                                                            'class': 'text-center'
                                                        },
                                                        'content': [
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-h4 font-weight-bold'
                                                                },
                                                                'text': f'{history[0].get("fnb", "—") if history else "—"}'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-caption'
                                                                },
                                                                'text': '当前飞牛币'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-body-2 mt-3'
                                                                },
                                                                'text': f'当前牛值: {history[0].get("nz", "—") if history else "—"}'
                                                            },
                                                            {
                                                                'component': 'div',
                                                                'props': {
                                                                    'class': 'text-body-2'
                                                                },
                                                                'text': f'登录天数: {history[0].get("login_days", "—") if history else "—"}'
                                                            }
                                                        ]
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    },
                    {
                        'component': 'VCol',
                        'props': {
                            'cols': 12,
                            'md': 3
                        },
                        'content': [
                            {
                                'component': 'VCard',
                                'props': {
                                    'variant': 'outlined',
                                    'class': 'mb-4',
                                    'color': 'secondary'
                                },
                                'content': [
                                    {
                                        'component': 'VCardTitle',
                                        'props': {
                                            'class': 'text-h6'
                                        },
                                        'text': '使用帮助'
                                    },
                                    {
                                        'component': 'VCardText',
                                        'content': [
                                            {
                                                'component': 'VList',
                                                'props': {
                                                    'lines': 'two',
                                                    'density': 'compact'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'VListItem',
                                                        'props': {
                                                            'prepend-icon': 'mdi-checkbox-marked-circle',
                                                            'title': '绿色状态表示成功',
                                                            'subtitle': '已成功完成签到任务'
                                                        }
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'props': {
                                                            'prepend-icon': 'mdi-alert-circle',
                                                            'title': '红色状态表示失败',
                                                            'subtitle': '签到失败，需检查原因'
                                                        }
                                                    },
                                                    {
                                                        'component': 'VListItem',
                                                        'props': {
                                                            'prepend-icon': 'mdi-refresh',
                                                            'title': '点击立即运行',
                                                            'subtitle': '可在设置中手动触发一次'
                                                        }
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            },
            {
                'component': 'VCard',
                'props': {
                    'variant': 'outlined',
                    'class': 'mt-4'
                },
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {
                            'class': 'd-flex align-center'
                        },
                        'content': [
                            {
                                'component': 'span',
                                'text': '📝 签到历史记录'
                            },
                            {
                                'component': 'VSpacer'
                            },
                            {
                                'component': 'VChip',
                                'props': {
                                    'color': 'primary',
                                    'size': 'small',
                                    'variant': 'outlined'
                                },
                                'text': f'共{run_count}条记录'
                            }
                        ]
                    },
                    {
                        'component': 'VCardText',
                        'content': [
                            {
                                'component': 'VTable',
                                'props': {
                                    'hover': True,
                                    'density': 'compact'
                                },
                                'content': [
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'tr',
                                                'content': [
                                                    {
                                                        'component': 'th',
                                                        'text': '日期'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '时间'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '状态'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '飞牛币'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '牛值'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '积分'
                                                    },
                                                    {
                                                        'component': 'th',
                                                        'text': '登录天数'
                                                    }
                                                ]
                                            }
                                        ]
                                    },
                                    {
                                        'component': 'tbody',
                                        'content': [
                                            {
                                                'component': 'tr',
                                                'v-for': 'item in history',
                                                'props': {
                                                    ':key': 'item.date',
                                                    ':class': '("签到成功" in item.status || "已签到" in item.status) ? "bg-success-subtle" : ("签到失败" in item.status ? "bg-error-subtle" : "")'
                                                },
                                                'content': [
                                                    {
                                                        'component': 'td',
                                                        'text': 'item.date.split(" ")[0]'
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': 'item.date.split(" ")[1]'
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'content': [
                                                            {
                                                                'component': 'VChip',
                                                                'props': {
                                                                    'size': 'x-small',
                                                                    ':color': '("签到成功" in item.status || "已签到" in item.status) ? "success" : ("签到失败" in item.status ? "error" : "warning")',
                                                                    'variant': 'tonal'
                                                                },
                                                                'text': 'item.status'
                                                            }
                                                        ]
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': 'item.fnb || "—"'
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': 'item.nz || "—"'
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': 'item.credit || "—"'
                                                    },
                                                    {
                                                        'component': 'td',
                                                        'text': 'item.login_days || "—"'
                                                    }
                                                ]
                                            }
                                        ]
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        try:
            if self._scheduler:
                self._scheduler.remove_all_jobs()
                if self._scheduler.running:
                    self._scheduler.shutdown()
                self._scheduler = None
        except Exception as e:
            logger.error(f"退出插件失败: {str(e)}")

    def get_command(self) -> List[Dict[str, Any]]:
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        return []

    def _check_cookie_valid(self, session):
        """检查Cookie是否有效"""
        try:
            # 访问个人空间
            profile_url = "https://club.fnnas.com/home.php?mod=space"
            response = session.get(profile_url)
            response.raise_for_status()

            # 检查是否需要登录
            if "请先登录后才能继续浏览" in response.text or "您需要登录后才能继续本操作" in response.text:
                logger.error("Cookie无效或已过期")
                return False

            # 尝试获取UID
            uid_pattern = r'home\.php\?mod=space&uid=(\d+)'
            uid_match = re.search(uid_pattern, response.text)
            
            if uid_match:
                uid = uid_match.group(1)
                logger.info(f"Cookie有效，当前用户UID: {uid}")
                
                # 访问用户空间页面尝试获取用户名
                try:
                    user_url = f"https://club.fnnas.com/home.php?mod=space&uid={uid}"
                    user_response = session.get(user_url)
                    
                    # 尝试多种方式获取用户名
                    username_patterns = [
                        r'<title>(.*?)的个人空间',
                        r'<h2 class="mt">(.*?)</h2>',
                        r'<strong class="mt">(.*?)</strong>'
                    ]
                    
                    for pattern in username_patterns:
                        username_match = re.search(pattern, user_response.text)
                        if username_match:
                            username = username_match.group(1).strip()
                            if username:
                                logger.info(f"识别到用户名: {username}")
                                break
                except Exception as e:
                    logger.debug(f"获取用户名失败: {str(e)}")
                
                return True
            else:
                # 尝试其他方式确认登录状态
                if "天天打卡" in response.text or "安全退出" in response.text or "我的主页" in response.text:
                    logger.warning("Cookie有效，但未找到UID")
                    return True
                else:
                    logger.error("Cookie无效，未检测到登录状态")
                    return False
                
        except Exception as e:
            logger.error(f"检查Cookie有效性时出错: {str(e)}")
            return False 

    def _extract_required_cookies(self, cookie_str):
        """从完整Cookie字符串中提取必要的Cookie值"""
        try:
            cookies = {}
            # 分割Cookie字符串
            parts = cookie_str.split(';')
            
            # 提取必要的Cookie值
            for part in parts:
                part = part.strip()
                if '=' not in part:
                    continue
                    
                name, value = part.split('=', 1)
                name = name.strip()
                
                # 只保留需要的Cookie
                if name in ['pvRK_2132_saltkey', 'pvRK_2132_auth']:
                    cookies[name] = value
            
            # 检查是否获取到必要的Cookie
            required_cookies = ['pvRK_2132_saltkey', 'pvRK_2132_auth']
            missing = [c for c in required_cookies if c not in cookies]
            
            if missing:
                logger.error(f"Cookie中缺少必要的值: {', '.join(missing)}")
                return None
                
            logger.info(f"成功提取必要的Cookie值: {', '.join(cookies.keys())}")
            return cookies
            
        except Exception as e:
            logger.error(f"解析Cookie时出错: {str(e)}")
            return None 

    def _is_manual_trigger(self):
        """
        检查是否为手动触发的签到
        手动触发的签到不应该被历史记录阻止
        """
        # 在调用堆栈中检查sign_in_api是否存在，若存在则为手动触发
        import inspect
        for frame in inspect.stack():
            if frame.function == 'sign_in_api':
                logger.info("检测到手动触发签到")
                return True
        
        if hasattr(self, '_manual_trigger') and self._manual_trigger:
            logger.info("检测到通过_onlyonce手动触发签到")
            self._manual_trigger = False
            return True
            
        return False

    def _is_already_signed_today(self):
        """
        检查今天是否已经成功签到过
        
        考虑两种情况：
        1. 通过查询历史记录判断今天是否已签到
        2. 如果昨天的签到是在23:50之后，今天早上的定时任务应该仍然执行
        """
        today = datetime.now().strftime('%Y-%m-%d')
        
        # 获取历史记录
        history = self.get_data('sign_history') or []
        
        # 获取最后一次成功签到的日期和时间
        last_sign_date = self.get_data('last_sign_date')
        if not last_sign_date:
            logger.info("未找到最后一次签到记录")
            return False
            
        # 解析最后一次签到的日期
        try:
            last_sign_datetime = datetime.strptime(last_sign_date, '%Y-%m-%d %H:%M:%S')
            last_sign_day = last_sign_datetime.strftime('%Y-%m-%d')
            
            # 如果最后一次签到是今天，检查是否在今天
            if last_sign_day == today:
                logger.info(f"今日已成功签到，时间: {last_sign_datetime.strftime('%H:%M:%S')}")
                return True
                
            # 如果最后一次签到是昨天，但时间太晚（例如23:50以后），今天也要签到
            yesterday = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
            if last_sign_day == yesterday:
                # 如果是昨天23:50以后签到的，今天也需要签到
                if last_sign_datetime.hour >= 23 and last_sign_datetime.minute >= 50:
                    logger.info(f"昨天深夜已签到 ({last_sign_datetime.strftime('%H:%M:%S')}), 但今天仍需要签到")
                    return False
        except Exception as e:
            logger.error(f"解析最后签到日期时出错: {str(e)}")
            return False
            
        return False
        
    def _save_last_sign_date(self):
        """
        保存最后一次成功签到的日期和时间
        """
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        self.save_data('last_sign_date', now)
        logger.info(f"记录签到成功时间: {now}") 