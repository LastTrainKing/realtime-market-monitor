// 资金流向指挥中心 — 主程序入口
#![cfg_attr(all(not(debug_assertions), target_os = "windows"), windows_subsystem = "windows")]

fn main() {
    capital_flow_center_lib::run()
}
