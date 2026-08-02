"""
视频后处理模块
在视频生成完成后，自动进行场景切分、质量评估和素材库管理

功能：
1. 场景检测和切分（使用 slicer）
2. 并行调用 Gemini 为每个分镜生成 caption 和质量评分
3. 提取首尾帧并拼接
4. 将所有信息存储到素材库，并用 CSV 记录
"""

import os
import csv
import json
import subprocess
import cv2
import numpy as np
from pathlib import Path
from typing import List, Dict, Tuple, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from PIL import Image


class VideoPostProcessor:
    """视频后处理器"""
    
    def __init__(
        self,
        gemini_api_key: str,
        asset_base_dir: str = "./asset_library",
        scene_threshold: float = 50.0,
        min_scene_length: int = 15,
        max_workers: int = 4,
        llm_model: str = "gemini-3-flash-preview"
    ):
        """
        初始化视频后处理器
        
        Args:
            gemini_api_key: Gemini API Key
            asset_base_dir: 素材库根目录
            scene_threshold: 场景检测阈值
            min_scene_length: 最小场景长度（帧数）
            max_workers: 并行处理线程数
            llm_model: Gemini 模型名称
        """
        self.gemini_api_key = gemini_api_key
        self.asset_base_dir = Path(asset_base_dir)
        self.scene_threshold = scene_threshold
        self.min_scene_length = min_scene_length
        self.max_workers = max_workers
        self.llm_model = llm_model
        
        # 确保素材库目录存在
        self.asset_base_dir.mkdir(parents=True, exist_ok=True)
        
        # 主 CSV 文件路径
        self.master_csv_path = self.asset_base_dir / "master_asset_library.csv"
        self._init_master_csv()
    
    def _init_master_csv(self):
        """初始化主 CSV 文件（如果不存在）"""
        if not self.master_csv_path.exists():
            fieldnames = [
                'asset_id',
                'source_video',
                'scene_id',
                'video_path',
                'thumbnail_path',
                'start_frame',
                'end_frame',
                'duration',
                'caption',
                'quality_score',
                'is_usable',
                'has_model_penetration',
                'has_ai_artifacts',
                'has_poor_aesthetics',
                'quality_issues',
                'created_at'
            ]
            
            with open(self.master_csv_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
            
            print(f"✅ 初始化主 CSV 文件: {self.master_csv_path}")
    
    def process_video(self, video_path: str, video_name: str = None) -> Dict:
        """
        处理单个视频：切分、评估、存储
        
        Args:
            video_path: 视频文件路径
            video_name: 视频名称（用于命名素材库文件夹）
        
        Returns:
            处理结果字典
        """
        if not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        
        # 生成素材库文件夹名称
        if video_name is None:
            video_name = Path(video_path).stem
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        asset_folder_name = f"{video_name}_{timestamp}"
        asset_folder = self.asset_base_dir / asset_folder_name
        asset_folder.mkdir(parents=True, exist_ok=True)
        
        print("=" * 80)
        
        print(f"🎬 开始处理视频: {video_name}")
        print(f"📂 素材库文件夹: {asset_folder_name}")
        print("=" * 80)
        
        # 步骤 1: 场景检测
        print("\n🔍 步骤 1: 场景检测...")
        scenes = self._detect_scenes(video_path)
        print(f"   检测到 {len(scenes)} 个场景")
        
        # 步骤 2: 切分视频
        print("\n✂️ 步骤 2: 切分视频...")
        scene_files = self._split_scenes(video_path, scenes, asset_folder)
        print(f"   切分成 {len(scene_files)} 个片段")
        
        # 步骤 3: 提取首尾帧并拼接
        print("\n🖼️ 步骤 3: 提取首尾帧...")
        for scene in scene_files:
            thumbnail_path = self._extract_and_combine_frames(
                scene['video_path'],
                asset_folder,
                scene['scene_id']
            )
            scene['thumbnail_path'] = thumbnail_path
        print(f"   生成 {len(scene_files)} 个缩略图")
        
        # 步骤 4: 并行生成 caption 和质量评分
        print(f"\n📝 步骤 4: 并行生成 caption 和质量评分 ({self.max_workers} 线程)...")
        evaluated_scenes = self._evaluate_scenes_parallel(scene_files)
        print(f"   完成 {len(evaluated_scenes)} 个场景的评估")
        
        # 步骤 5: 保存到 CSV
        print("\n💾 步骤 5: 保存到素材库...")
        asset_csv_path = asset_folder / "scenes.csv"
        self._save_to_csv(evaluated_scenes, asset_csv_path, video_path)
        self._append_to_master_csv(evaluated_scenes, video_path)
        
        # 步骤 6: 保存元数据
        metadata = {
            'source_video': video_path,
            'video_name': video_name,
            'asset_folder': str(asset_folder),
            'total_scenes': len(evaluated_scenes),
            'usable_scenes': sum(1 for s in evaluated_scenes if s.get('is_usable', True)),
            'average_quality': sum(s.get('quality_score', 0) for s in evaluated_scenes) / len(evaluated_scenes) if evaluated_scenes else 0,
            'created_at': datetime.now().isoformat(),
            'scenes': evaluated_scenes
        }
        
        metadata_path = asset_folder / "metadata.json"
        with open(metadata_path, 'w', encoding='utf-8') as f:
            json.dump(metadata, f, indent=2, ensure_ascii=False)
        
        print("\n" + "=" * 80)
        print("✅ 视频处理完成！")
        print(f"📊 统计信息:")
        print(f"   - 总场景数: {metadata['total_scenes']}")
        print(f"   - 可用场景: {metadata['usable_scenes']}/{metadata['total_scenes']}")
        print(f"   - 平均质量: {metadata['average_quality']:.2f}/10")
        print(f"📂 素材库位置: {asset_folder}")
        print(f"📄 场景 CSV: {asset_csv_path}")
        print(f"📄 元数据: {metadata_path}")
        print("=" * 80)
        
        return metadata
    
    def _detect_scenes(self, video_path: str) -> List[Tuple[int, int]]:
        """
        基于阈值的场景检测
        
        Returns:
            场景列表 [(start_frame, end_frame), ...]
        """
        cap = cv2.VideoCapture(video_path)
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        
        scene_changes = [0]
        
        ret, prev_frame = cap.read()
        if not ret:
            cap.release()
            return [(0, total_frames - 1)]
        
        prev_gray = cv2.cvtColor(prev_frame, cv2.COLOR_BGR2GRAY)
        frame_idx = 1
        last_scene_start = 0
        
        while True:
            ret, curr_frame = cap.read()
            if not ret:
                break
            
            curr_gray = cv2.cvtColor(curr_frame, cv2.COLOR_BGR2GRAY)
            diff = cv2.absdiff(prev_gray, curr_gray)
            mean_diff = np.mean(diff)
            
            if mean_diff > self.scene_threshold and (frame_idx - last_scene_start) >= self.min_scene_length:
                scene_changes.append(frame_idx)
                last_scene_start = frame_idx
            
            prev_gray = curr_gray
            frame_idx += 1
        
        cap.release()
        
        scene_changes.append(total_frames - 1)
        scenes = [(scene_changes[i], scene_changes[i + 1] - 1) 
                  for i in range(len(scene_changes) - 1)]
        
        return scenes
    
    def _split_scenes(
        self,
        video_path: str,
        scenes: List[Tuple[int, int]],
        output_dir: Path
    ) -> List[Dict]:
        """使用 ffmpeg 切分场景"""
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS)
        cap.release()
        
        scene_files = []
        
        for i, (start_frame, end_frame) in enumerate(scenes, 1):
            start_time = self._frame_to_timestamp(start_frame, fps)
            duration = (end_frame - start_frame + 1) / fps
            
            output_file = output_dir / f"scene_{i:03d}.mp4"
            
            cmd = [
                'ffmpeg', '-y',
                '-ss', start_time,
                '-i', video_path,
                '-t', str(duration),
                '-c:v', 'libx264',
                '-preset', 'fast',
                '-crf', '23',
                '-c:a', 'aac',
                str(output_file)
            ]
            
            try:
                subprocess.run(
                    cmd,
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
                
                scene_files.append({
                    'scene_id': i,
                    'video_path': str(output_file),
                    'start_frame': start_frame,
                    'end_frame': end_frame,
                    'duration': duration,
                    'start_time': start_time
                })
                
            except subprocess.CalledProcessError as e:
                print(f"   ❌ 场景 {i} 切分失败: {e}")
        
        return scene_files
    
    def _extract_and_combine_frames(
        self,
        video_path: str,
        output_dir: Path,
        scene_id: int
    ) -> str:
        """
        提取首尾帧并横向拼接为一张图
        
        Returns:
            拼接后的图片路径
        """
        cap = cv2.VideoCapture(video_path)
        
        # 提取首帧
        ret, first_frame = cap.read()
        if not ret:
            cap.release()
            return None
        
        # 提取尾帧
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.set(cv2.CAP_PROP_POS_FRAMES, total_frames - 1)
        ret, last_frame = cap.read()
        cap.release()
        
        if not ret:
            last_frame = first_frame
        
        # 转换为 PIL Image
        first_img = Image.fromarray(cv2.cvtColor(first_frame, cv2.COLOR_BGR2RGB))
        last_img = Image.fromarray(cv2.cvtColor(last_frame, cv2.COLOR_BGR2RGB))
        
        # 横向拼接
        width, height = first_img.size
        combined = Image.new('RGB', (width * 2, height))
        combined.paste(first_img, (0, 0))
        combined.paste(last_img, (width, 0))
        
        # 保存
        thumbnail_path = output_dir / f"scene_{scene_id:03d}_thumbnail.jpg"
        combined.save(thumbnail_path, quality=90)
        
        return str(thumbnail_path)
    
    def _evaluate_scenes_parallel(self, scene_files: List[Dict]) -> List[Dict]:
        """并行生成场景描述和质量评估"""
        from clients import get_llm_client
        import json
        import mimetypes
        
        def evaluate_single_scene(scene: Dict) -> Dict:
            """为单个场景生成描述和质量评估"""
            client = get_llm_client(step="video_critique")
            
            evaluation_schema = {
                "type": "object",
                "properties": {
                    "caption": {
                        "type": "string",
                        "description": "场景内容的简洁描述（1-2句话）"
                    },
                    "quality_score": {
                        "type": "number",
                        "description": "整体质量评分（0-10分）"
                    },
                    "is_usable": {
                        "type": "boolean",
                        "description": "镜头是否可用（无严重问题）"
                    },
                    "has_model_penetration": {
                        "type": "boolean",
                        "description": "是否存在穿模问题"
                    },
                    "has_ai_artifacts": {
                        "type": "boolean",
                        "description": "是否存在明显的AI生成瑕疵"
                    },
                    "has_poor_aesthetics": {
                        "type": "boolean",
                        "description": "美感是否差"
                    },
                    "quality_issues": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "具体的质量问题列表"
                    }
                },
                "required": ["caption", "quality_score", "is_usable", 
                            "has_model_penetration", "has_ai_artifacts", 
                            "has_poor_aesthetics", "quality_issues"]
            }
            
            prompt = """
请仔细观看这段视频片段，进行全面的质量评估。

**评估维度：**

1. **场景描述（caption）**：用1-2句话描述视频内容
   - 主要人物或物体
   - 正在发生的动作或事件
   - 画面的视角或镜头特点（如特写、全景、跟拍等）
   - 突出的视觉元素（颜色、光线、特效等）

2. **质量评分**（0-10分）：
   - 9-10分：完美，无明显瑕疵
   - 7-8分：良好，可直接使用
   - 5-6分：一般，有轻微问题但可接受
   - 3-4分：较差，有明显问题
   - 0-2分：很差，不可用

3. **质量问题检测**：
   - **穿模问题**：人物/物体相互穿透，物理不合理
   - **AI瑕疵**：手指畸形、面部扭曲、物体变形、时间不连贯
   - **美感问题**：构图失衡、光线生硬、色彩不协调、运镜僵硬

4. **可用性判断**：
   - 如果存在严重的穿模、AI瑕疵或美感问题，标记为不可用
   - 轻微问题可标记为可用

请以 JSON 格式返回评估结果。
"""
            
            try:
                # 使用 gemini_wrapper 的 generate_with_video 方法
                # 启用自动 FPS 调整和低分辨率模式
                response_text = client.generate_with_video(
                    text_prompt=prompt,
                    video_paths=[scene['video_path']],
                    temperature=0.3,
                    response_schema=evaluation_schema,
                    model=self.llm_model,
                    auto_adjust_fps=True,      # 自动调整帧率
                    use_low_resolution=True    # 使用低分辨率（节省 token）
                )
                
                evaluation = json.loads(response_text)
                
                scene['caption'] = evaluation.get('caption', '描述生成失败')
                scene['quality_score'] = evaluation.get('quality_score', 0)
                scene['is_usable'] = evaluation.get('is_usable', False)
                scene['has_model_penetration'] = evaluation.get('has_model_penetration', False)
                scene['has_ai_artifacts'] = evaluation.get('has_ai_artifacts', False)
                scene['has_poor_aesthetics'] = evaluation.get('has_poor_aesthetics', False)
                scene['quality_issues'] = evaluation.get('quality_issues', [])
                
                status = "✅" if scene['is_usable'] else "❌"
                print(f"   场景 {scene['scene_id']:03d}: {status} 质量 {scene['quality_score']:.1f}/10")
                
            except Exception as e:
                print(f"   ❌ 场景 {scene['scene_id']} 评估失败: {e}")
                scene['caption'] = "评估失败"
                scene['quality_score'] = 0
                scene['is_usable'] = False
                scene['has_model_penetration'] = False
                scene['has_ai_artifacts'] = False
                scene['has_poor_aesthetics'] = False
                scene['quality_issues'] = [f"评估失败: {str(e)}"]
            
            return scene
        
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(evaluate_single_scene, scene): scene 
                      for scene in scene_files}
            
            results = []
            for future in as_completed(futures):
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    print(f"   ❌ 任务失败: {e}")
        
        results.sort(key=lambda x: x['scene_id'])
        return results
    
    def _save_to_csv(self, scenes: List[Dict], csv_path: Path, source_video: str):
        """保存场景数据到 CSV 文件"""
        fieldnames = [
            'scene_id',
            'video_path',
            'thumbnail_path',
            'start_frame',
            'end_frame',
            'duration',
            'caption',
            'quality_score',
            'is_usable',
            'has_model_penetration',
            'has_ai_artifacts',
            'has_poor_aesthetics',
            'quality_issues'
        ]
        
        with open(csv_path, 'w', newline='', encoding='utf-8') as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            
            for scene in scenes:
                row = {
                    'scene_id': scene.get('scene_id', ''),
                    'video_path': scene.get('video_path', ''),
                    'thumbnail_path': scene.get('thumbnail_path', ''),
                    'start_frame': scene.get('start_frame', ''),
                    'end_frame': scene.get('end_frame', ''),
                    'duration': f"{scene.get('duration', 0):.2f}",
                    'caption': scene.get('caption', ''),
                    'quality_score': scene.get('quality_score', 0),
                    'is_usable': scene.get('is_usable', False),
                    'has_model_penetration': scene.get('has_model_penetration', False),
                    'has_ai_artifacts': scene.get('has_ai_artifacts', False),
                    'has_poor_aesthetics': scene.get('has_poor_aesthetics', False),
                    'quality_issues': '; '.join(scene.get('quality_issues', []))
                }
                writer.writerow(row)
    
    def _append_to_master_csv(self, scenes: List[Dict], source_video: str):
        """将场景数据追加到主 CSV 文件"""
        with open(self.master_csv_path, 'a', newline='', encoding='utf-8') as f:
            fieldnames = [
                'asset_id',
                'source_video',
                'scene_id',
                'video_path',
                'thumbnail_path',
                'start_frame',
                'end_frame',
                'duration',
                'caption',
                'quality_score',
                'is_usable',
                'has_model_penetration',
                'has_ai_artifacts',
                'has_poor_aesthetics',
                'quality_issues',
                'created_at'
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            
            for scene in scenes:
                asset_id = f"{Path(source_video).stem}_scene_{scene['scene_id']:03d}"
                row = {
                    'asset_id': asset_id,
                    'source_video': source_video,
                    'scene_id': scene.get('scene_id', ''),
                    'video_path': scene.get('video_path', ''),
                    'thumbnail_path': scene.get('thumbnail_path', ''),
                    'start_frame': scene.get('start_frame', ''),
                    'end_frame': scene.get('end_frame', ''),
                    'duration': f"{scene.get('duration', 0):.2f}",
                    'caption': scene.get('caption', ''),
                    'quality_score': scene.get('quality_score', 0),
                    'is_usable': scene.get('is_usable', False),
                    'has_model_penetration': scene.get('has_model_penetration', False),
                    'has_ai_artifacts': scene.get('has_ai_artifacts', False),
                    'has_poor_aesthetics': scene.get('has_poor_aesthetics', False),
                    'quality_issues': '; '.join(scene.get('quality_issues', [])),
                    'created_at': datetime.now().isoformat()
                }
                writer.writerow(row)
    
    def _frame_to_timestamp(self, frame: int, fps: float) -> str:
        """将帧号转换为时间戳"""
        seconds = frame / fps
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        secs = seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:06.3f}"


def process_video_simple(
    video_path: str,
    gemini_api_key: str = None,
    asset_base_dir: str = "./asset_library",
    video_name: str = None
) -> Dict:
    """
    简化的视频处理函数
    
    Args:
        video_path: 视频文件路径
        gemini_api_key: Gemini API Key（如果为 None，从环境变量读取）
        asset_base_dir: 素材库根目录
        video_name: 视频名称
    
    Returns:
        处理结果字典
    """
    if gemini_api_key is None:
        gemini_api_key = os.environ.get("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("请提供 Gemini API Key 或设置 GEMINI_API_KEY 环境变量")
    
    processor = VideoPostProcessor(
        gemini_api_key=gemini_api_key,
        asset_base_dir=asset_base_dir
    )
    
    return processor.process_video(video_path, video_name)


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) < 2:
        print("用法: python video_post_processor.py <video_path> [video_name]")
        sys.exit(1)
    
    video_path = sys.argv[1]
    video_name = sys.argv[2] if len(sys.argv) > 2 else None
    
    result = process_video_simple(video_path, video_name=video_name)
    print(f"\n✅ 处理完成！素材库位置: {result['asset_folder']}")
