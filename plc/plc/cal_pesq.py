import argparse
from scipy.io import wavfile
from pesq import pesq

def calculate_pesq(reference_path, test_path, sample_rate=16000):
    """
    计算参考音频和测试音频之间的PESQ分数
    
    参数:
        reference_path: 参考音频WAV文件路径
        test_path: 测试音频WAV文件路径
        sample_rate: 采样率（支持8000或16000 Hz）
    
    返回:
        PESQ分数（范围：-0.5 ~ 4.5）
    """
    # 读取WAV文件
    ref_rate, ref_signal = wavfile.read(reference_path)
    test_rate, test_signal = wavfile.read(test_path)
    
    # 检查采样率是否一致
    if ref_rate != test_rate:
        raise ValueError(f"采样率不匹配: 参考文件 {ref_rate} Hz, 测试文件 {test_rate} Hz")
    
    # 检查是否支持该采样率
    if ref_rate not in [8000, 16000]:
        raise ValueError(f"不支持的采样率 {ref_rate} Hz，仅支持8000或16000 Hz")
    
    # 确保音频是单声道（PESQ要求）
    if len(ref_signal.shape) > 1:
        ref_signal = ref_signal[:, 0]  # 取左声道
    if len(test_signal.shape) > 1:
        test_signal = test_signal[:, 0]  # 取左声道
    
    # 计算PESQ分数
    score = pesq(ref_rate, ref_signal, test_signal, 'nb')  # 'wb'表示宽带(16kHz), 'nb'表示窄带(8kHz)
    return score

if __name__ == "__main__":
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='计算两个WAV文件的PESQ分数')
    parser.add_argument('reference', help='参考音频WAV文件路径')
    parser.add_argument('test', help='测试音频WAV文件路径')
    parser.add_argument('--sample-rate', type=int, default=16000, 
                      help='采样率（默认16000 Hz，支持8000或16000）')
    
    args = parser.parse_args()
    
    try:
        # 计算并打印PESQ分数
        pesq_score = calculate_pesq(args.reference, args.test, args.sample_rate)
        print(f"PESQ分数: {pesq_score:.3f}")
    except Exception as e:
        print(f"计算失败: {str(e)}")
