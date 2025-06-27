from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.edge.service import Service
import time
import os
import requests
import json
import psutil  # 导入 psutil 库
import argparse  # 导入 argparse 来处理命令行参数
import sys  # 导入 sys 来处理 PyInstaller 冻结模式
import io  # 导入 io 模块用于处理流编码


# --- PyInstaller 打包时的资源路径处理函数 ---
# 虽然此脚本内部已使用 __file__ 和 sys.argv[0] 处理路径，
# 但为了保持一致性，仍然保留这个辅助函数（虽然在本脚本中没有直接调用）
def get_resource_path(relative_path):
    """
    获取资源文件的绝对路径，适用于开发环境和 PyInstaller 打包后的环境。
    在 PyInstaller 单文件模式下，sys._MEIPASS 指向临时解压目录。
    """
    if hasattr(sys, '_MEIPASS'):
        # PyInstaller 打包后，资源在临时目录
        base_path = sys._MEIPASS
        print(f"[DEBUG - htmlget.py] 检测到 PyInstaller 冻结模式，基础路径: {base_path}")
    else:
        # 开发环境下，资源在脚本所在目录
        base_path = os.path.dirname(os.path.abspath(__file__))
        print(f"[DEBUG - htmlget.py] 检测到脚本模式，基础路径: {base_path}")

    full_path = os.path.join(base_path, relative_path)
    print(f"[DEBUG - htmlget.py] 资源 '{relative_path}' 的完整路径: {full_path}")
    return full_path


# 解析命令行参数 (用于 future_use, e.g., --frozen flag)
parser = argparse.ArgumentParser(description="HTML 获取和数据处理脚本。")
parser.add_argument('--frozen', action='store_true', help='指示脚本是否在 PyInstaller 冻结环境中运行。')
args = parser.parse_args()

# 获取当前脚本所在的目录
# 在 PyInstaller 单文件模式下，__file__ 指向临时解压目录中的脚本路径
current_script_dir = os.path.dirname(os.path.abspath(__file__))
print(f"[DEBUG - htmlget.py] 当前脚本目录: {current_script_dir}")

# --- 配置 ---
# <--- 重要：请更新此路径，确保它指向您的 msedgedriver.exe 文件！
# 示例：如果您把 msedgedriver.exe 放在和 htmlget.py 同一个目录下，可以这样写：
EDGE_DRIVER_PATH = os.path.join(current_script_dir, "msedgedriver.exe")
# 或者，如果您有固定路径，例如：
# EDGE_DRIVER_PATH = "C:/path/to/your/msedgedriver.exe"
print(f"[DEBUG - htmlget.py] Edge 驱动器路径: {EDGE_DRIVER_PATH}")

# 目标 URLs
TARGET_URL_INDEX = "https://id5.163.com/index.html"
TARGET_URL_ROOT = "https://id5.163.com/"

# --- 公共输出和图片保存目录 ---
OUTPUT_AND_IMAGE_DIR = os.path.join(current_script_dir, 'res')  # res 目录位于脚本所在的临时目录中

if not os.path.exists(OUTPUT_AND_IMAGE_DIR):
    os.makedirs(OUTPUT_AND_IMAGE_DIR)
    print(f"[DEBUG - htmlget.py] 已创建输出和图片保存目录: {OUTPUT_AND_IMAGE_DIR}")
else:
    print(f"[DEBUG - htmlget.py] 输出和图片保存目录已存在: {OUTPUT_AND_IMAGE_DIR}")

# 输出文件名 (JSON 文件)
OUTPUT_FILENAME = "web_data.json"
output_file_path = os.path.join(OUTPUT_AND_IMAGE_DIR, OUTPUT_FILENAME)
print(f"[DEBUG - htmlget.py] 数据文件输出路径: {output_file_path}")


# --- 定义终止 WebDriver 进程的函数 ---
def terminate_webdriver_process(driver_path):
    """
    尝试终止所有与指定 driver_path 关联的 WebDriver 进程。
    """
    driver_name = os.path.basename(driver_path)
    print(f"[DEBUG - htmlget.py] 尝试终止所有 {driver_name} 进程...")
    for proc in psutil.process_iter(['pid', 'name', 'cmdline']):
        try:
            # 检查进程名，并且检查命令行参数是否包含驱动路径，以避免误杀
            if proc.info['name'] == driver_name and driver_path in ' '.join(proc.info['cmdline']):
                print(
                    f"[DEBUG - htmlget.py] 找到匹配的进程: PID={proc.info['pid']}, 名称={proc.info['name']}, 命令行={proc.info['cmdline']}")
                proc.terminate()  # 尝试优雅地终止进程
                proc.wait(timeout=5)  # 等待5秒让进程终止

                if proc.is_running():  # 如果进程仍在运行，则强制杀死
                    proc.kill()
                    print(f"[DEBUG - htmlget.py] 强制终止了进程: {proc.info['pid']} ({driver_name})")
                else:
                    print(f"[DEBUG - htmlget.py] 优雅终止了进程: {proc.info['pid']} ({driver_name})")
            # else:
            #     print(f"[DEBUG - htmlget.py] 跳过不匹配的进程: PID={proc.info['pid']}, 名称={proc.info['name']}")

        except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess) as e:
            print(
                f"[DEBUG - htmlget.py] 终止进程 {proc.info['pid']} ({driver_name}) 时遇到问题: {e}，可能已退出或无权限。")
        except Exception as e:
            print(f"[ERROR - htmlget.py] 终止进程 {proc.info['pid']} ({driver_name}) 时发生未知错误: {e}")


# --- 设置 Edge 浏览器选项为无头模式 ---
edge_options = Options()
edge_options.add_argument("--headless")
edge_options.add_argument("--disable-gpu")
edge_options.add_argument("--no-sandbox")
edge_options.add_argument("--disable-dev-shm-usage")
print("[DEBUG - htmlget.py] Edge 浏览器已配置为无头模式。")

# --- 初始化 Edge WebDriver ---
edge_service = Service(executable_path=EDGE_DRIVER_PATH)

print("[DEBUG - htmlget.py] 正在以无头模式初始化 Edge 浏览器...")
driver = None  # 初始化 driver 为 None，以防创建失败
try:
    driver = webdriver.Edge(service=edge_service, options=edge_options)
    print("[DEBUG - htmlget.py] Edge WebDriver 初始化成功。")

    # 字典，用于存储所有抓取的数据
    all_data = {}
    all_data['news_list'] = []  # 用于存储新闻列表

    # --- 第一次导航：获取 'cb' 第一个元素的数据（赛季和背景图） ---
    print(f"\n--- [DEBUG - htmlget.py] 正在访问: {TARGET_URL_ROOT} 以获取赛季和背景图信息 ---")
    driver.get(TARGET_URL_ROOT)
    driver.implicitly_wait(10)  # 隐式等待，等待元素出现，可以防止因网速慢导致的元素未加载问题
    print(f"[DEBUG - htmlget.py] 已加载页面: {TARGET_URL_ROOT}")

    js_code_cb_first_element = """
    const cbElements = document.getElementsByClassName("cb");
    let cbData = null;
    if (cbElements.length > 0) {
        const firstElement = cbElements[0];
        if (firstElement) {
            let fullInnerText = firstElement.innerText || '';
            let filteredInnerText = '';
            const dotIndex = fullInnerText.indexOf('·');

            if (dotIndex !== -1) {
                filteredInnerText = fullInnerText.substring(0, dotIndex).trim();
            } else {
                filteredInnerText = fullInnerText.trim();
            }

            cbData = {
                href: firstElement.href || '',
                innerText: filteredInnerText
            };
        }
    }
    return cbData;
    """
    print("[DEBUG - htmlget.py] 正在执行 JavaScript 代码以提取赛季和背景图数据...")
    cb_extracted_data = driver.execute_script(js_code_cb_first_element)

    if cb_extracted_data:
        season_text = cb_extracted_data.get('innerText', 'N/A')
        background_img_url = cb_extracted_data.get('href', 'N/A')
        all_data['season'] = season_text  # <--- 存储到字典
        all_data['background_img'] = background_img_url  # <--- 存储到字典
        print(f"[DEBUG - htmlget.py] 提取到赛季: '{season_text}', 背景图 URL: '{background_img_url}'")

        # --- 添加背景图片下载逻辑 ---
        if background_img_url != 'N/A' and background_img_url.startswith('http'):
            try:
                bg_img_filename = "bg_img.jpg"
                bg_img_save_path = os.path.join(OUTPUT_AND_IMAGE_DIR, bg_img_filename)

                print(f"[DEBUG - htmlget.py] 正在下载背景图片: {background_img_url} 到 {bg_img_save_path}")
                response = requests.get(background_img_url, stream=True)
                response.raise_for_status()  # 检查HTTP请求是否成功

                with open(bg_img_save_path, 'wb') as img_file:
                    for chunk in response.iter_content(chunk_size=8192):
                        img_file.write(chunk)
                print(f"[DEBUG - htmlget.py] 背景图片下载成功: {bg_img_filename}")
            except requests.exceptions.RequestException as req_err:
                print(f"[ERROR - htmlget.py] 下载背景图片时发生网络错误 {background_img_url}: {req_err}")
            except Exception as e:
                print(f"[ERROR - htmlget.py] 下载或保存背景图片 {background_img_url} 时发生未知错误: {e}")
        else:
            print(f"[DEBUG - htmlget.py] 无效的背景图片 URL，跳过下载: {background_img_url}")
    else:
        all_data['season'] = 'N/A'
        all_data['background_img'] = 'N/A'
        print(f"[DEBUG - htmlget.py] 未在 '{TARGET_URL_ROOT}' 找到 'cb' 元素或相关属性。\n")

    # --- 第二次导航：获取 '.swiper-lazy.swiper-lazy-loaded' 元素数据（新闻） ---
    print(f"\n--- [DEBUG - htmlget.py] 正在访问: {TARGET_URL_INDEX} 以获取新闻列表 ---")
    driver.get(TARGET_URL_INDEX)
    driver.implicitly_wait(10)  # 隐式等待
    print(f"[DEBUG - htmlget.py] 已加载页面: {TARGET_URL_INDEX}")

    js_code_swiper = """
    const swiperImages = document.querySelectorAll(".swiper-lazy.swiper-lazy-loaded");
    const swiperData = [];
    for (let i = 0; i < Math.min(4, swiperImages.length); i++) {
        const imgElement = swiperImages[i];
        const aElement = imgElement.closest('a');

        let newsHref = '';
        if (aElement && aElement.href) {
            // 确保链接是完整的URL
            if (aElement.href.startsWith('/')) {
                newsHref = 'https://id5.163.com' + aElement.href;
            } else {
                newsHref = aElement.href;
            }
        }

        let newsTime = '';
        const itemDiv = imgElement.closest('.item');
        if (itemDiv) {
            const timeDiv = itemDiv.querySelector('.time');
            if (timeDiv) {
                const timeSpan = timeDiv.querySelector('span');
                if (timeSpan) {
                    newsTime = timeSpan.innerText.trim();
                }
            }
        }

        swiperData.push({
            alt: imgElement.alt,
            src: imgElement.src,
            href: newsHref,
            time: newsTime
        });
    }
    return swiperData;
    """
    print("[DEBUG - htmlget.py] 正在执行 JavaScript 代码以提取新闻数据、链接和时间...")
    swiper_extracted_data = driver.execute_script(js_code_swiper)

    if swiper_extracted_data:
        for i, item in enumerate(swiper_extracted_data):
            news_entry = {  # <--- 构建新闻字典
                "title": item.get('alt', 'N/A'),
                "src_url": item.get('src', 'N/A'),
                "link_url": item.get('href', 'N/A'),
                "time": item.get('time', 'N/A')
            }
            all_data['news_list'].append(news_entry)  # <--- 添加到新闻列表
            print(
                f"[DEBUG - htmlget.py] 提取到新闻 {i + 1}: 标题='{news_entry['title']}', 链接='{news_entry['link_url']}', 时间='{news_entry['time']}'")

            # --- 添加新闻图片下载逻辑 ---
            if news_entry['src_url'] != 'N/A' and news_entry['src_url'].startswith('http'):
                try:
                    news_img_filename = f"new{i + 1}_img.jpg"
                    news_img_save_path = os.path.join(OUTPUT_AND_IMAGE_DIR, news_img_filename)

                    print(f"[DEBUG - htmlget.py] 正在下载新闻图片: {news_entry['src_url']} 到 {news_img_save_path}")
                    response = requests.get(news_entry['src_url'], stream=True)
                    response.raise_for_status()

                    with open(news_img_save_path, 'wb') as image_file:
                        for chunk in response.iter_content(chunk_size=8192):
                            image_file.write(chunk)
                    print(f"[DEBUG - htmlget.py] 新闻图片下载成功: {news_img_filename}")
                except requests.exceptions.RequestException as req_err:
                    print(f"[ERROR - htmlget.py] 下载新闻图片时发生网络错误 {news_entry['src_url']}: {req_err}")
                except Exception as e:
                    print(f"[ERROR - htmlget.py] 下载或保存新闻图片 {news_entry['src_url']} 时发生未知错误: {e}")
            else:
                print(f"[DEBUG - htmlget.py] 无效的新闻图片 URL，跳过下载: {news_entry['src_url']}")
    else:
        print(f"[DEBUG - htmlget.py] 未在 '{TARGET_URL_INDEX}' 找到新闻元素。")

    # --- 将所有提取到的数据写入 JSON 文件 ---
    with open(output_file_path, "w", encoding="utf-8") as f:
        json.dump(all_data, f, ensure_ascii=False, indent=4)  # <--- 使用 json.dump 写入 JSON
    print(f"\n[DEBUG - htmlget.py] 数据已成功写入到 JSON 文件: {output_file_path}")

except Exception as e:
    print(f"[ERROR - htmlget.py] 发生错误: {e}")  # 打印捕获到的错误

finally:
    # --- 清理：尝试关闭浏览器，然后强制终止可能残留的进程 ---
    if driver:
        try:
            print("\n[DEBUG - htmlget.py] 正在尝试关闭浏览器 (driver.quit())...")
            driver.quit()
            print("[DEBUG - htmlget.py] 浏览器已关闭。")
        except Exception as e:
            print(f"[ERROR - htmlget.py] 关闭浏览器时遇到错误 (driver.quit() 失败): {e}")
    # 无论 driver.quit() 是否成功，或 driver 是否被初始化，都尝试终止 WebDriver 进程
    # 这会捕获并清理任何可能残留的 msedgedriver.exe 进程
    terminate_webdriver_process(EDGE_DRIVER_PATH)
    print("[DEBUG - htmlget.py] WebDriver 进程清理完成。")

