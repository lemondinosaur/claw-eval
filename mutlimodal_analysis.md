# Multimodal Tasks Data Source Analysis

## 范围
- 本文基于 `tasks/M001` 到 `tasks/M101` 的 `task.yaml` 和 `fixtures` 目录整理。
- 这里把“数据源”拆成两层：
- 主源数据：任务直接给模型的原始输入，比如视频、图片、PDF、JSON 配置。
- 辅助源数据：为了构题、校验、补充时效事实而额外需要的来源，比如网页快照、字幕文本、角色参考图、价格表、POI 地址库。

## 总览
- `M` 任务一共 101 个。
- 最核心的主源数据其实只有 5 类：`视频`、`图片`、`PDF`、`JSON 配置`、`多源组合（视频+外部事实 / PDF+外部事实）`。
- 真正决定构题成本的，不是媒体格式本身，而是是否还要补“第二数据源”。

## A. 配置/单图驱动的网页或 SVG 复现
- 任务：`M001_clock`, `M002_world_clock`, `M003_solar_system`, `M004_countdown_fireworks`, `M005_score_canon`, `M006_score_mariage`, `M007_score_symphony`, `M008_metro_map_1`, `M009_metro_map_2`, `M010_score_canon_animated`, `M011_score_mariage_animated`, `M012_score_symphony_animated`, `M013_metro_route_1`, `M014_metro_route_2`, `M052_webpage_recreation`
- 主数据源：
- `M001` 到 `M004` 主要吃 `config.json` / `planets.json` 一类结构化配置。
- `M005` 到 `M014`、`M052` 主要吃单张参考图：乐谱图、地铁图、网页截图。
- 数据源怎么找：
- 配置型任务最容易自造，直接程序生成 JSON 即可。
- 乐谱可从公开乐谱站、PDF 截图、教材扫描页获取。
- 地铁图优先用官方线路图或清晰导出图。
- 网页复现图可以来自自建网页截图、Dribbble/Figma 社区稿、公开 landing page 截图。
- 构造时需要什么：
- 一张分辨率足够高、结构清楚的静态图，或者一份字段定义清楚的配置 JSON。
- 如果想稳定评测网页渲染效果，最好同时准备浏览器录帧脚本或参考渲染图。

## B. 视频 OCR / 字幕时序抽取
- 任务：`M015_video_subtitle_ocr_english`, `M016_video_subtitle_ocr_chinese_filter`, `M017_video_subtitle_ocr_timestamp`
- 主数据源：
- 带硬字幕、闪字、时间窗明确的短视频。
- 数据源怎么找：
- YouTube/B 站/Douyin 的解说视频、财经视频、教育视频都适合。
- 优先找字幕大、背景相对干净、进入/消失边界清楚的片段。
- 构造时需要什么：
- 原始视频。
- 精确到片段级的人工标注：哪些词出现、哪些句子命中关键词、每条字幕的起止时间。
- 如果题目要求去重、过滤、时间窗限制，最好先整理一份标准答案表。

## C. 论文 PDF 抽取 / 多文档汇总 / 图表复现
- 任务：`M018_doc_extraction_line_chart`, `M019_doc_extraction_radar_chart`, `M020_multi_doc_extraction_bar_chart`, `M021_doc_reference_verification`, `M073_doc_extraction_training_cost`, `M074_doc_extraction_thinking_impact`, `M075_doc_extraction_spatial_leaderboard`, `M076_doc_extraction_cross_table_merge`, `M077_doc_extraction_cross_modality`, `M078_doc_extraction_cross_benchmark`, `M079_doc_extraction_f1_verification`, `M080_doc_extraction_delta_comparison`, `M081_doc_extraction_heatmap_comparison`, `M082_multi_doc_extraction_scatter`, `M083_multi_doc_extraction_horizontal_bar`, `M084_doc_figure_reproduction_pie`, `M085_doc_figure_reproduction_bar`, `M086_doc_figure_reproduction_line`, `M087_multi_doc_extraction_grouped_bar`
- 主数据源：
- 单篇 PDF：`M018`, `M019`, `M021`, `M073`, `M074`, `M075`, `M076`, `M077`, `M078`, `M079`, `M080`, `M081`, `M084`, `M085`, `M086`
- 多篇 PDF：`M020`, `M082`, `M083`, `M087`
- 数据源怎么找：
- 首选 arXiv、CVF OpenAccess、ACL/EMNLP/NeurIPS/OpenReview 等稳定公开 PDF。
- 最好选“表格清楚、图号明确、模型名规范、版本稳定”的论文。
- 构造时需要什么：
- 论文 PDF 本体。
- 明确的抽取锚点：表号、图号、列名、模型名、指标名。
- 如果题目要求复现图表，最好额外保存目标图的参考截图。
- 特别说明：
- `M018`, `M020`, `M079`, `M080`, `M081`, `M082`, `M083`, `M087` 在 prompt 里直接给公网 PDF URL。构题时要考虑 URL 失效，最好本地缓存一份。
- `M019`, `M021`, `M074`, `M075`, `M084`, `M085`, `M086` 已经把 PDF 放进了 `fixtures`，这种方式最稳。
- `M073`, `M076`, `M077`, `M078` 只给论文标题，不给 URL。构题时需要额外确定唯一可访问 PDF。

## D. 体育赛事视频事件统计 / 规则理解
- 任务：`M025_video_badminton_match_qa`, `M028_video_badminton_score_chart`, `M032_video_tennis_rally_qa`, `M033_video_tennis_breakpoint_qa`, `M034_video_tennis_shotlog_qa`, `M035_video_tennis_exhibition_qa`, `M053_video_badminton_rally_count`, `M054_video_badminton_match_analysis`, `M055_video_badminton_baseline_out`, `M056_video_badminton_net_error`, `M057_video_pingpong_rally_count`, `M058_video_pingpong_serve_stats`, `M059_video_pingpong_smash_ace`, `M060_video_pingpong_let_serve`, `M061_video_snooker_clearance_sequence`, `M062_video_snooker_brown_ball_time`, `M063_video_soccer_goal_analysis`, `M064_video_soccer_save_analysis`, `M065_video_tennis_net_error`, `M066_video_tennis_lob_winner`, `M067_video_tennis_long_rally`, `M068_video_tennis_set_point_analysis`, `M069_video_tennis_ace_return_ace`, `M070_video_tennis_serve_game_stats`, `M071_video_tennis_break_point_stats`, `M072_video_tennis_set_end_time`
- 主数据源：
- 单场比赛片段、集锦、单局视频。
- 主要是羽毛球、网球、乒乓球、斯诺克、足球。
- 数据源怎么找：
- 最适合的来源是官方赛事集锦和转播切片，比如 ATP/WTA、澳网、温网、BWF、ITTF、FIFA、央视体育、ESPN 类公开片段。
- 优先选计分牌清晰、镜头稳定、事件边界清楚的视频。
- 构造时需要什么：
- 原始比赛视频。
- 人工事件标注：发球方、比分变化、回合数、球种、犯规类型、关键球类型、局末时间点。
- 如果题目涉及图表输出，还需要把标准事件序列整理成结构化表。
- 这类任务的难点不是“找视频”，而是“做高质量标注”。没有可靠标注，题就不稳。

## E. 开放域单视频理解 / 总结 / 信息整理 / 网页生成
- 任务：`M022_video_movie_recognition`, `M023_video_paper_understanding`, `M024_video_factory_promo_webpage`, `M026_video_story_interactive_webpage`, `M027_video_food_memo`, `M030_video_snack_checklist`, `M031_video_room_floorplan`, `M036_video_butterfly_drawing_tutorial`, `M037_video_food_shop_search`, `M042_video_mme_multihop_reasoning`, `M046_video_mme_news_segments`, `M047_video_fitness_exercise_summary`, `M048_video_fitness_pullup_frames`, `M049_video_phone_comparison`, `M050_video_shopping_receipt`, `M098_video_craft_webpage`
- 主数据源：
- vlog、纪录片、工厂宣传片、科普/论文讲解、手工教程、购物记录、室内漫游、新闻视频。
- 数据源怎么找：
- B 站、YouTube、企业官网宣传片、纪录片短切、教程视频都可以。
- 选材时优先找“主题集中、可抽取实体多、镜头不太碎、字幕或旁白足够清楚”的视频。
- 构造时需要什么：
- 原始视频。
- 任务目标对应的实体清单或摘要标注，比如零食名单、房间布局、工艺品列表、教程步骤、新闻段落边界。
- 如果最后要生成网页，最好再准备 3 到 10 张参考成品图，便于稳定评测页面视觉结果。
- 这一类里最典型的“第二数据源”任务是 `M023_video_paper_understanding`, `M037_video_food_shop_search`, `M049_video_phone_comparison`。

## F. 视频定位 / 抽帧 / 裁剪 / 剪辑 / 影视片段任务
- 任务：`M029_video_surveillance_clip`, `M038_video_lvb_hill_descent`, `M039_video_lvb_machine_dog`, `M040_video_lvb_vehicle_identification`, `M041_video_lvb_artwork_scene`, `M043_video_mme_device_identification`, `M044_video_mme_bugatti_identification`, `M045_video_mme_building_identification`, `M051_video_surveillance_intrusion`, `M088_video_movie_clip_extraction`, `M089_video_movie_scene_meme`, `M090_video_movie_qa_wedding`, `M091_video_movie_qa_flashback`, `M092_video_movie_title_localization`, `M093_video_movie_concat_subtitle`, `M094_video_movie_band_extraction`, `M095_video_movie_character_id`, `M096_video_movie_title_director`, `M097_video_movie_speed_edit`
- 主数据源：
- 监控视频、公开视频片段、电影切片、场景定位视频、多片段素材。
- 数据源怎么找：
- 可以来自公开视频平台，也可以从自有/授权长视频里手工剪出目标片段。
- 这类题最适合“目标事件非常明确”的素材，比如某人出场、某辆车绕圈、片名打在屏幕上、角色露脸、乐队演奏段落。
- 构造时需要什么：
- 原始视频，必要时是两个视频源，比如 `M093_video_movie_concat_subtitle`。
- 精确时间戳。
- 关键帧或裁剪目标。
- 如果涉及角色识别，还需要名字表或参考人脸图。
- 这类题常常还要准备额外的标准产物，比如目标截图、标准 meme 样式、片名帧、角色参考图。

## G. 单图识别 / 图像 + 外部事实
- 任务：`M099_su7_price_from_image_zh`, `M100_su7_price_from_image`, `M101_chinese_food_identification_zh`
- 主数据源：
- 单张商品图、车辆图、美食图。
- 数据源怎么找：
- 自拍图、电商图、宣传图、媒体图都可以。
- 适合选“主体清楚、可辨识特征明显、背景干扰少”的图片。
- 构造时需要什么：
- 原始图片。
- 对应的标准答案或别名集合。
- 如果问题是“现在卖多少钱”这种时效题，还必须补官方价格或可信二级来源，并固定日期。

## 哪些任务明显需要“第二数据源”
- `M021_doc_reference_verification`：除了 PDF，还需要截止日期对应的论文正式发表状态来源。
- `M023_video_paper_understanding`：除了视频，还需要原论文和代码仓库状态。
- `M037_video_food_shop_search`：除了 vlog，还需要地图/POI/商家地址数据。
- `M045_video_mme_building_identification`：除了视频，还需要建筑高度的权威来源，并锁定日期。
- `M049_video_phone_comparison`：如果视频里参数不全，需要补官方参数表和价格信息。
- `M096_video_movie_title_director`：如果片段里没有直接给导演，需要电影元数据来源。
- `M099_su7_price_from_image_zh`, `M100_su7_price_from_image`：除了图片，还需要当前价格来源。
- 影视识别类任务如 `M022_video_movie_recognition`, `M090_video_movie_qa_wedding`, `M091_video_movie_qa_flashback`, `M092_video_movie_title_localization`, `M095_video_movie_character_id`，构题时也建议留一份片名/角色名答案表，不然评测会很脆弱。

## 仓库里已经出现的辅助夹具模式
- `take_recording.py`：主要用于网页任务截图评测，见 `M001` 到 `M014`、`M024`、`M026`。
- `gt.png` / `gt_*.png`：主要是视觉对齐参考图，见 `M037`, `M084`, `M085`, `M086`, `M089`, `M092`, `M098`。
- `subtitle.txt`：给视频推理补文本锚点，见 `M042_video_mme_multihop_reasoning`。
- 角色参考图：见 `M095_video_movie_character_id`。
- `oracle.json`：别名或标准答案表，见 `M101_chinese_food_identification_zh`。

## 一个简单结论
- 如果你后面要继续扩 `M` 任务，最容易稳定扩的是 3 类：
- `PDF 抽取类`：数据公开、答案容易结构化。
- `体育视频类`：素材多，但标注成本高。
- `单图/单视频到网页类`：素材好找，视觉评测也比较直接。
- 最容易踩坑的是“媒体本身不够，必须再查现实世界事实”的任务，因为这类题会受到日期、网页更新、实体消歧的影响。
