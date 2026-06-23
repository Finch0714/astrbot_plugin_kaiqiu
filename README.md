# astrbot_plugin_kaiqiu — 开球网数据查询插件

基于 [AstrBot](https://github.com/Soulter/AstrBot) 框架，查询开球网（kaiqiuwang.com）球员与赛事数据。

## 指令

| 指令 | 说明 | 示例 |
|------|------|------|
| `/kqw player <名称>` | 搜索球员 | `/kqw player 马龙` |
| `/查看 <序号>` | 查看球员详细档案（头像、积分、战绩、比赛记录） | `/查看 1` |
| `/下一页` | 翻页查看比赛记录 | `/下一页` |
| `/kqw match` | 启动赛事查询（分步交互） | `/kqw match` |
| `/搜索 <城市名>` | 输入查询城市 | `/搜索 杭州` |
| `/搜索 <数字>` | 选择时间范围（1本月 2今年 3近三月） | `/搜索 1` |

## 使用示例

```
# 球员查询
/kqw player 马龙
/查看 1
/下一页

# 赛事查询
/kqw match
/搜索 杭州
/搜索 1
```

## 安装

将本插件目录放入 `AstrBot/data/plugins/`，在 WebUI 中启用即可。

## 项目结构

```
astrbot_plugin_kaiqiu/
├── main.py          # 插件主入口
├── metadata.yaml    # 插件元数据
├── logo.png         # 插件图标
├── README.md        # 本文件
├── LICENSE
└── .gitignore
```

## 许可证

MIT
