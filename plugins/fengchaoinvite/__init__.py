import random
import json
import time
import re
from datetime import datetime, timedelta
from typing import Any, List, Dict, Tuple, Optional

import pytz # 确保导入 pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from app.core.config import settings
from app.core.event import eventmanager
from app.helper.cookie import CookieHelper
from app.log import logger
from app.plugins import _PluginBase
from app.utils.http import RequestUtils
from app.utils.string import StringUtils
from app.schemas import NotificationType


class FengchaoInvite(_PluginBase):
    # 插件名称
    plugin_name = "蜂巢邀请监控"
    # 插件描述
    plugin_desc = "监控蜂巢论坛待审核邀请，并实时推送到通知渠道"
    # 插件图标
    plugin_icon = "https://raw.githubusercontent.com/madrays/MoviePilot-Plugins/main/icons/fengchao.png"
    # 插件版本
    plugin_version = "1.0.0"
    # 插件作者
    plugin_author = "madrays"
    # 作者主页
    author_url = "https://github.com/madrays/MoviePilot-Plugins"
    # 插件配置项ID前缀
    plugin_config_prefix = "fengchaoinvite_"
    # 加载顺序
    plugin_order = 31
    # 可使用的用户级别
    auth_level = 2

    # 私有属性
    _enabled = False
    _notify = False
    _cron = None
    _onlyonce = False
    _proxy = None
    _username = None
    _password = None
    _check_interval = None
    _pending_reviews = None
    _retry_count = None
    _retry_interval = None
    _use_proxy = True
    
    # 定时器
    _scheduler: Optional[BackgroundScheduler] = None

    def init_plugin(self, config: dict = None):
        # 停止现有任务
        self.stop_service()

        if config:
            self._enabled = config.get("enabled", False)
            self._notify = config.get("notify", True)
            self._cron = config.get("cron")
            self._onlyonce = config.get("onlyonce", False)
            self._username = config.get("username")
            self._password = config.get("password")
            self._check_interval = config.get("check_interval", 5)
            self._retry_count = int(config.get("retry_count", 3)) # 确保是整数
            self._retry_interval = int(config.get("retry_interval", 5)) # 确保是整数
            self._use_proxy = config.get("use_proxy", True)
            self._pending_reviews = self.get_data('pending_reviews') or {}

        # 启动服务
        if self._enabled:
            # 创建独立的 scheduler 实例
            self._scheduler = BackgroundScheduler(timezone=settings.TZ)
            
            if self._onlyonce:
                self.info(f"监控蜂巢论坛邀请...")
                # 立即执行一次检查，使用 run_date
                self._scheduler.add_job(func=self.check_invites, trigger='date',
                                   run_date=datetime.now(tz=pytz.timezone(settings.TZ)) + timedelta(seconds=3),
                                   id=f"{self.__class__.__name__}_check_invite_once",
                                   name=f"蜂巢邀请监控服务 (一次性)")
                # 关闭一次性开关
                self._onlyonce = False
                # 注意：更新配置的操作应该在基类或 MoviePilot 核心中完成，插件内部通常不需要手动调用 update_config
                # self.update_config({...}) # 移除或注释掉插件内部的 update_config 调用
            
            # 添加周期性任务
            if self._cron:
                self.info(f"监控蜂巢论坛邀请服务启动，定时任务：{self._cron}")
                try:
                    # 使用 CronTrigger.from_crontab
                    self._scheduler.add_job(func=self.check_invites,
                                            trigger=CronTrigger.from_crontab(self._cron),
                                            id=f"{self.__class__.__name__}_check_invite_cron",
                                            name=f"蜂巢邀请监控服务 (Cron)")
                except Exception as e:
                    logger.error(f"添加 Cron 任务失败: {str(e)}")
            # 添加间隔任务（仅当没有 cron 时）
            elif self._check_interval and int(self._check_interval) > 0: 
                self.info(f"监控蜂巢论坛邀请服务启动，间隔：{self._check_interval}分钟")
                try:
                    self._scheduler.add_job(func=self.check_invites,
                                            trigger="interval",
                                            minutes=int(self._check_interval),
                                            id=f"{self.__class__.__name__}_check_invite_interval",
                                            name=f"蜂巢邀请监控服务 (间隔)")
                except Exception as e:
                    logger.error(f"添加 Interval 任务失败: {str(e)}")
            
            # 启动 scheduler (如果添加了任务)
            if self._scheduler and self._scheduler.get_jobs():
                try:
                    self._scheduler.start()
                    logger.info(f"蜂巢邀请监控服务的 Scheduler 已启动")
                except Exception as e:
                    logger.error(f"启动 Scheduler 失败: {str(e)}")
                    self._scheduler = None # 启动失败则重置
        else:
            logger.info("蜂巢邀请监控插件未启用")
            
    def get_state(self) -> bool:
        """
        获取插件状态
        """
        return self._enabled
    
    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册命令
        """
        return []

    def get_api(self) -> List[Dict[str, Any]]:
        """
        注册API
        """
        return []

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册服务 (如果需要对外提供)
        """
        # 如果 check_invites 主要是由内部 scheduler 调用，这里可以返回 []
        # 如果希望 MoviePilot 核心也能管理这个任务（例如在服务页面显示），则可以保留
        # 但要注意不要重复调度
        # 为了保持与之前的行为类似，暂时保留，但 func 指向 self.check_invites
        # if self._enabled and self._cron:
        #     try:
        #         return [{
        #             "id": f"{self.__class__.__name__}_service", # 使用唯一的服务 ID
        #             "name": "蜂巢邀请监控 (服务注册)",
        #             "trigger": CronTrigger.from_crontab(self._cron),
        #             "func": self.check_invites, # 指向实例方法
        #             "kwargs": {}
        #         }]
        #     except Exception as e:
        #         logger.error(f"注册蜂巢邀请监控服务失败: {str(e)}")
        return [] # 推荐返回空列表，由插件内部 scheduler 管理
    
    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        """
        return [
            {
                'component': 'VForm',
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
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 用户名密码输入
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'username',
                                            'label': '用户名',
                                            'placeholder': '蜂巢论坛用户名',
                                            'hint': '请输入蜂巢论坛用户名'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'password',
                                            'label': '密码',
                                            'placeholder': '蜂巢论坛密码',
                                            'type': 'password',
                                            'hint': '请输入蜂巢论坛密码'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 监控周期和重试设置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VCronField',
                                        'props': {
                                            'model': 'cron',
                                            'label': '定时周期',
                                            'placeholder': '*/5 * * * *',
                                            'hint': '填写cron表达式，留空则使用固定间隔'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'check_interval',
                                            'label': '固定间隔(分钟)',
                                            'placeholder': '5',
                                            'hint': '未配置cron表达式时使用，每隔多少分钟检查一次'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 失败重试设置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'retry_count',
                                            'label': '失败重试次数',
                                            'type': 'number',
                                            'placeholder': '3',
                                            'hint': '请求失败重试次数'
                                        }
                                    }
                                ]
                            },
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VTextField',
                                        'props': {
                                            'model': 'retry_interval',
                                            'label': '重试间隔(秒)',
                                            'type': 'number',
                                            'placeholder': '5',
                                            'hint': '请求失败多少秒后重试'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 代理设置
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {
                                    'cols': 12,
                                    'md': 6
                                },
                                'content': [
                                    {
                                        'component': 'VSwitch',
                                        'props': {
                                            'model': 'use_proxy',
                                            'label': '使用代理',
                                            'hint': '与蜂巢论坛通信时使用系统代理'
                                        }
                                    }
                                ]
                            }
                        ]
                    },
                    # 提示
                    {
                        'component': 'VRow',
                        'content': [
                            {
                                'component': 'VCol',
                                'props': {'cols': 12},
                                'content': [
                                    {
                                        'component': 'VAlert',
                                        'props': {
                                            'type': 'info',
                                            'variant': 'tonal',
                                            'text': '此插件用于监控蜂巢论坛的邀请审核状态，当有新的待审核邀请或邀请长时间未审核时，将通过MoviePilot通知系统推送信息。'
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
            "cron": "*/5 * * * *",
            "onlyonce": False,
            "username": "",
            "password": "",
            "check_interval": 5,
            "retry_count": 3,
            "retry_interval": 5,
            "use_proxy": True
        }

    def get_page(self) -> List[dict]:
        """
        构建插件详情页面，展示邀请历史
        """
        # 获取邀请历史
        historys = self.get_data('pending_reviews') or {}
        
        # 如果没有历史记录
        if not historys:
            return [
                {
                    'component': 'VAlert',
                    'props': {
                        'type': 'info',
                        'variant': 'tonal',
                        'text': '暂无邀请记录，请先配置用户名密码并启用插件',
                        'class': 'mb-2'
                    }
                }
            ]
        
        # 处理历史记录
        history_items = []
        for item_id, timestamp in historys.items():
            if isinstance(timestamp, str):
                try:
                    timestamp_dt = datetime.fromisoformat(timestamp)
                    date_str = timestamp_dt.strftime('%Y-%m-%d %H:%M:%S')
                except:
                    date_str = timestamp
            else:
                date_str = str(timestamp)
                
            history_items.append({
                'id': item_id,
                'date': date_str
            })
        
        # 按时间倒序排列
        history_items.sort(key=lambda x: x['date'], reverse=True)
        
        # 构建历史记录表格行
        history_rows = []
        for item in history_items[:30]:  # 只显示最近30条
            history_rows.append({
                'component': 'tr',
                'content': [
                    # 邀请ID列
                    {
                        'component': 'td',
                        'text': item['id']
                    },
                    # 记录时间列
                    {
                        'component': 'td',
                        'text': item['date']
                    }
                ]
            })
        
        # 最终页面组装
        return [
            # 标题
            {
                'component': 'VCard',
                'props': {'variant': 'outlined', 'class': 'mb-4'},
                'content': [
                    {
                        'component': 'VCardTitle',
                        'props': {'class': 'text-h6'},
                        'text': '📊 蜂巢论坛邀请监控记录'
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
                                    # 表头
                                    {
                                        'component': 'thead',
                                        'content': [
                                            {
                                                'component': 'tr',
                                                'content': [
                                                    {'component': 'th', 'text': '邀请ID'},
                                                    {'component': 'th', 'text': '记录时间'}
                                                ]
                                            }
                                        ]
                                    },
                                    # 表内容
                                    {
                                        'component': 'tbody',
                                        'content': history_rows
                                    }
                                ]
                            }
                        ]
                    }
                ]
            }
        ]

    def stop_service(self):
        """
        停止服务并清理 scheduler
        """
        try:
            if self._scheduler:
                if self._scheduler.running:
                    # 先移除所有任务，防止任务仍在执行时关闭 scheduler 出错
                    self._scheduler.remove_all_jobs()
                    # 关闭 scheduler
                    self._scheduler.shutdown()
                    logger.info("蜂巢邀请监控服务的 Scheduler 已关闭")
                self._scheduler = None # 将引用置为 None
        except Exception as e:
            # 使用 error 记录停止服务失败
            self.error(f"停止服务失败: {str(e)}")

    def check_invites(self):
        """
        检查待审核邀请
        """
        if not self._enabled:
            return
        
        self.info(f"开始检查蜂巢论坛待审核邀请...")

        if not self._username or not self._password:
            self.error("用户名或密码未配置，无法检查待审核邀请")
            self.send_msg("蜂巢邀请监控", "用户名或密码未配置，无法检查待审核邀请")
            return

        # 登录获取Cookie
        cookie = self._login_and_get_cookie()
        if not cookie:
            self.error("登录失败，无法获取Cookie")
            self.send_msg("蜂巢邀请监控", "登录失败，无法获取Cookie")
            return

        # 检查待审核邀请
        self._check_invites_with_cookie(cookie)

    def _login_and_get_cookie(self):
        """
        登录蜂巢论坛并获取cookie
        """
        self.info("开始登录蜂巢论坛...")
        
        # 初始化请求工具
        req_utils = RequestUtils(
            proxy=settings.PROXY if self._use_proxy else None,
            timeout=30
        )
        
        try:
            # 第一步：GET请求获取CSRF和初始cookie
            self.debug("步骤1: GET请求获取CSRF和初始cookie...")
            res = req_utils.get_res("https://pting.club")
            if not res or res.status_code != 200:
                self.error(f"访问蜂巢论坛失败，状态码：{res.status_code if res else '未知'}")
                return None

            # 从网页内容中提取CSRF令牌
            csrf_token = None
            pattern = r'"csrfToken":"(.*?)"'
            csrf_matches = re.findall(pattern, res.text)
            csrf_token = csrf_matches[0] if csrf_matches else None
            if not csrf_token:
                self.error("无法获取CSRF令牌")
                return None
            
            self.debug(f"获取到CSRF令牌: {csrf_token}")

            # 从响应头中获取初始session cookie
            cookies = res.cookies.get_dict()
            if not cookies or 'flarum_session' not in cookies:
                self.error("无法获取初始session cookie")
                return None
            
            session_cookie = cookies.get('flarum_session')
            self.debug(f"获取到session cookie")

            # 第二步：POST请求登录
            self.debug("步骤2: POST请求登录...")
            login_data = {
                "identification": self._username,
                "password": self._password,
                "remember": True
            }
            login_headers = {
                "Content-Type": "application/json",
                "X-CSRF-Token": csrf_token,
                "Accept": "*/*",
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
            }
            
            # 发送登录请求
            login_res = req_utils.post_res(
                url="https://pting.club/login",
                json=login_data,
                headers=login_headers,
                cookies=cookies
            )
            
            if not login_res or login_res.status_code != 200:
                self.error(f"登录请求失败，状态码：{login_res.status_code if login_res else '未知'}")
                return None
            
            self.debug(f"登录请求成功，状态码: {login_res.status_code}")

            # 获取登录后的cookies
            login_cookies = login_res.cookies.get_dict()
            cookies.update(login_cookies)
            
            # 构建cookie字符串
            cookie_str = "; ".join([f"{k}={v}" for k, v in cookies.items()])
            self.info("登录成功，获取到cookie")
            
            return cookie_str
            
        except Exception as e:
            self.error(f"登录过程中发生异常: {str(e)}")
            return None

    def _check_invites_with_cookie(self, cookie, max_retries=None, retry_delay=None):
        """
        使用cookie检查待审核邀请
        """
        if max_retries is None:
            # 确保使用整数类型的重试次数
            max_retries = int(self._retry_count) 
        if retry_delay is None:
            # 确保使用整数类型的重试间隔
            retry_delay = int(self._retry_interval)
            
        url = "https://pting.club/api/store/invite/list"
        params = {
            'filter[query]': "",
            'filter[status]': "0",
            'page[offset]': "0"
        }
        headers = {
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.0.0 Safari/537.36",
            'Cookie': cookie
        }
        
        req_utils = RequestUtils(
            proxy=settings.PROXY if self._use_proxy else None,
            timeout=30
        )
        
        retries = 0
        while retries <= max_retries:
            try:
                response = req_utils.get_res(url, params=params, headers=headers)
                if not response or response.status_code != 200:
                    self.error(f"获取待审核邀请失败，状态码：{response.status_code if response else '未知'}")
                    retries += 1
                    if retries <= max_retries:
                        self.debug(f"第{retries}/{max_retries}次重试...")
                        time.sleep(retry_delay)
                    continue
                
                try:
                    data = response.json()
                except Exception as e:
                    self.error(f"解析响应数据失败: {str(e)}")
                    return
                
                if data.get('data'):
                    self.info(f"发现{len(data['data'])}个待审核邀请")
                    
                    notification_items = []
                    current_pending_reviews = {}  # 当前待审核邀请的集合
                    
                    for item in data['data']:
                        item_id = item['id']  # 假设每个item有唯一的id
                        current_pending_reviews[item_id] = datetime.now()  # 记录当前时间
                        
                        # 检查是否是新的待审核邀请或超过4小时未审核的邀请
                        is_new = item_id not in self._pending_reviews
                        is_overtime = False
                        
                        if not is_new:
                            last_time = self._pending_reviews.get(item_id)
                            if isinstance(last_time, str):
                                try:
                                    last_time = datetime.fromisoformat(last_time)
                                except:
                                    last_time = None
                                    
                            # 确保 last_time 是 datetime 对象再比较
                            if last_time and isinstance(last_time, datetime) and (datetime.now() - last_time).total_seconds() > 4 * 3600:
                                is_overtime = True
                        
                        if is_new or is_overtime:
                            # 提取邀请信息
                            # 使用 .get() 避免 KeyError
                            attributes = item.get('attributes', {})
                            user = attributes.get('user', '未知')
                            email = attributes.get('email', '未知')
                            username = attributes.get('username', '未知')
                            link = attributes.get('link', '未知')
                            link2 = attributes.get('link2', '未知')
                            
                            # 添加到通知列表
                            notification_items.append({
                                "邀请人": user,
                                "邮箱": email,
                                "用户名": username,
                                "链接1": link,
                                "链接2": link2,
                                "状态": "新邀请" if is_new else "超过4小时未审核"
                            })
                            
                            self.debug(f"{'新增' if is_new else '超时'}待审核邀请: {item_id}")
                    
                    # 发送通知
                    if notification_items and self._notify:
                        self._send_invites_notification(notification_items)
                    
                    # 更新记录
                    # 将datetime对象转换为ISO格式字符串进行存储
                    self._pending_reviews = {k: v.isoformat() for k, v in current_pending_reviews.items()}
                    self.save_data('pending_reviews', self._pending_reviews)
                
                else:
                    self.info("没有待审核的邀请")
                    if self._pending_reviews: # 仅当之前有记录时才重置
                        self._pending_reviews = {}  # 重置记录
                        self.save_data('pending_reviews', self._pending_reviews)
                
                # 成功获取数据，跳出循环
                break
                
            except Exception as e:
                self.error(f"检查待审核邀请过程中发生异常: {str(e)}")
                retries += 1
                if retries <= max_retries:
                    self.debug(f"第{retries}/{max_retries}次重试...")
                    time.sleep(retry_delay)
                else:
                    self.error(f"已达到最大重试次数 ({max_retries})，请求失败")
                    break # 达到最大次数后也跳出循环

    def _send_invites_notification(self, items):
        """
        发送邀请通知
        """
        if not items:
            return
            
        try:
            # 构建通知内容
            title = f"蜂巢论坛 - 待审核邀请 ({len(items)}个)"
            
            # 构建详细文本
            text = "## 蜂巢论坛待审核邀请\n\n"
            
            for i, item in enumerate(items, 1):
                status = item.get("状态", "待审核")
                text += f"### 邀请 {i} ({status})\n"
                text += f"- 邀请人：{item.get('邀请人', '未知')}\n"
                text += f"- 邮箱：{item.get('邮箱', '未知')}\n"
                text += f"- 用户名：{item.get('用户名', '未知')}\n"
                text += f"- 链接1：{item.get('链接1', '未知')}\n"
                text += f"- 链接2：{item.get('链接2', '未知')}\n\n"
            
            # 发送通知
            self.send_msg(title=title, text=text)
            self.info(f"已发送{len(items)}个待审核邀请通知")
            
        except Exception as e:
            self.error(f"发送通知失败: {str(e)}")

    def send_msg(self, title, text="", image=""):
        """
        发送消息
        """
        if not self._notify:
            return
        
        try:
            self.post_message(mtype=NotificationType.SiteMessage, title=title, text=text)
        except Exception as e:
            self.error(f"发送通知失败: {str(e)}")


plugin_class = FengchaoInvite