# 当前已验证工作流导出

`package_workflow(service, workflow_id, output)` 从既有service读取状态，不写workflow数据库或正式库。
最后一个history项必须validate/passed，其committed_revision必须等于当前snapshot.revision，
environment.validation事实必须就是该项receipt。validation body还必须绑定当前input、scene、replay
和observation；当前visual必须passed。收集前后、导出后均重读snapshot，变化时拒绝交付（已产生候选
目录保留，不能当作当前已验收包）。

收集对象是input、当前intent/asset/scene/replay/observation/visual/validation与历史stage receipts
可达CAS图。模型日志和失败operation中宿主已有error/model日志也保留；不递归读取任意本机路径、
runtime目录或原资产库。当前representation资产成员由export_package保持logical paths；历史资产
原字节作为.evidence报告保存，避免旧URDF副本被当成现行入口。provenance保存完整引用→相对路径
映射、workflow revision、修订history和当前事实；图像和连续MP4至少各一个，否则拒绝。

返回manifest_path/package_path及明确checks。member integrity可passed，relocated_execution与
physical_profile仍not_run：package_workflow不运行Genesis。用户或调用方随后调用包内run.py或
`run_package(package_path, output=NEW_DIR, runtime_roots=..., denied_roots=...)`验证复制后的执行；
无需再提供一层包装函数。policy/data仍false；development_unqualified不升级成release。

静态公共seam测试覆盖未验证/失败/旧revision拒绝和当前validation导出输入、媒体、history。
测试中的physics authority是受控consumer fixture，不是真实仿真；此切片没有新增昂贵回放。
