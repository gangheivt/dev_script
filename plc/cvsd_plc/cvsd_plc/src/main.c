#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include < assert.h >
#include "audio_msbc_plc.h"
#include "audio_cvsd.h"
#include "audio_filter.h"

int g_total = 0;
int g_error = 0;
int g_error1 = 0;

typedef struct audio_cvsd_tag
{
    cvsd_t cvsd_e;//encode
    cvsd_t cvsd_d;//decode
    uint8_t* bit_buf;
    int16_t* inp_buf;
    int16_t* out_buf;
    int16_t* interpolate_buf;
    int16_t* decimate_buf;
    int16_t* inp_buf_shift;
    int16_t* out_buf_shift;
    int buf_size_FIR_assumpt;
    int out_len_interpolate;
    int out_len_interp_FIR_assumpt;
} audio_cvsd_t;

audio_cvsd_t g_audio_cvsd_env =
{
    .bit_buf = NULL,
    .inp_buf = NULL,
    .out_buf = NULL,
    .interpolate_buf = NULL,
    .decimate_buf = NULL,
    .inp_buf_shift = NULL,
    .out_buf_shift = NULL,
};
LowcFE_c g_plc;
#define BT_CVSD_FRAME_LEN  60
static void* calloc_buffer(void* src, int size, int n)
{
    if ((src = calloc(n, sizeof(size))) == NULL)
    {
        printf("Error in memory allocation!\n");
        //exit(1);
    }
    return src;
}
void bt_cvsd_init(void)
{
    int pow_M_L_factor = 3;

    g_audio_cvsd_env.buf_size_FIR_assumpt = BT_CVSD_FRAME_LEN + FIR_FILTER_LENGTH;

    g_audio_cvsd_env.out_len_interpolate = BT_CVSD_FRAME_LEN << pow_M_L_factor;
    g_audio_cvsd_env.out_len_interp_FIR_assumpt = g_audio_cvsd_env.out_len_interpolate + FIR_FILTER_LENGTH;

    g_audio_cvsd_env.bit_buf = (uint8_t*)calloc_buffer(g_audio_cvsd_env.bit_buf, sizeof(uint8_t), BT_CVSD_FRAME_LEN);

    g_audio_cvsd_env.inp_buf = (int16_t*)calloc_buffer(g_audio_cvsd_env.inp_buf, sizeof(int16_t), g_audio_cvsd_env.buf_size_FIR_assumpt);
    g_audio_cvsd_env.out_buf = (int16_t*)calloc_buffer(g_audio_cvsd_env.out_buf, sizeof(int16_t), g_audio_cvsd_env.out_len_interp_FIR_assumpt);
    g_audio_cvsd_env.interpolate_buf = (int16_t*)calloc_buffer(g_audio_cvsd_env.interpolate_buf, sizeof(int16_t), g_audio_cvsd_env.out_len_interpolate);
    g_audio_cvsd_env.decimate_buf = (int16_t*)calloc_buffer(g_audio_cvsd_env.decimate_buf, sizeof(int16_t), BT_CVSD_FRAME_LEN);
    g_audio_cvsd_env.inp_buf_shift = (int16_t*)(g_audio_cvsd_env.inp_buf + FIR_FILTER_LENGTH);
    g_audio_cvsd_env.out_buf_shift = (int16_t*)(g_audio_cvsd_env.out_buf + FIR_FILTER_LENGTH);

    if (cvsdInit(&g_audio_cvsd_env.cvsd_e))
    {
        printf("incorrect initialization of CVSD!\n");
        //exit(1);
    }

    if (cvsdInit(&g_audio_cvsd_env.cvsd_d))
    {
        printf("incorrect initialization of CVSD!\n");
        //exit(1);
    }
}
static const unsigned char table[256] =
{
    0x00, 0x80, 0x40, 0xc0, 0x20, 0xa0, 0x60, 0xe0,
    0x10, 0x90, 0x50, 0xd0, 0x30, 0xb0, 0x70, 0xf0,
    0x08, 0x88, 0x48, 0xc8, 0x28, 0xa8, 0x68, 0xe8,
    0x18, 0x98, 0x58, 0xd8, 0x38, 0xb8, 0x78, 0xf8,
    0x04, 0x84, 0x44, 0xc4, 0x24, 0xa4, 0x64, 0xe4,
    0x14, 0x94, 0x54, 0xd4, 0x34, 0xb4, 0x74, 0xf4,
    0x0c, 0x8c, 0x4c, 0xcc, 0x2c, 0xac, 0x6c, 0xec,
    0x1c, 0x9c, 0x5c, 0xdc, 0x3c, 0xbc, 0x7c, 0xfc,
    0x02, 0x82, 0x42, 0xc2, 0x22, 0xa2, 0x62, 0xe2,
    0x12, 0x92, 0x52, 0xd2, 0x32, 0xb2, 0x72, 0xf2,
    0x0a, 0x8a, 0x4a, 0xca, 0x2a, 0xaa, 0x6a, 0xea,
    0x1a, 0x9a, 0x5a, 0xda, 0x3a, 0xba, 0x7a, 0xfa,
    0x06, 0x86, 0x46, 0xc6, 0x26, 0xa6, 0x66, 0xe6,
    0x16, 0x96, 0x56, 0xd6, 0x36, 0xb6, 0x76, 0xf6,
    0x0e, 0x8e, 0x4e, 0xce, 0x2e, 0xae, 0x6e, 0xee,
    0x1e, 0x9e, 0x5e, 0xde, 0x3e, 0xbe, 0x7e, 0xfe,
    0x01, 0x81, 0x41, 0xc1, 0x21, 0xa1, 0x61, 0xe1,
    0x11, 0x91, 0x51, 0xd1, 0x31, 0xb1, 0x71, 0xf1,
    0x09, 0x89, 0x49, 0xc9, 0x29, 0xa9, 0x69, 0xe9,
    0x19, 0x99, 0x59, 0xd9, 0x39, 0xb9, 0x79, 0xf9,
    0x05, 0x85, 0x45, 0xc5, 0x25, 0xa5, 0x65, 0xe5,
    0x15, 0x95, 0x55, 0xd5, 0x35, 0xb5, 0x75, 0xf5,
    0x0d, 0x8d, 0x4d, 0xcd, 0x2d, 0xad, 0x6d, 0xed,
    0x1d, 0x9d, 0x5d, 0xdd, 0x3d, 0xbd, 0x7d, 0xfd,
    0x03, 0x83, 0x43, 0xc3, 0x23, 0xa3, 0x63, 0xe3,
    0x13, 0x93, 0x53, 0xd3, 0x33, 0xb3, 0x73, 0xf3,
    0x0b, 0x8b, 0x4b, 0xcb, 0x2b, 0xab, 0x6b, 0xeb,
    0x1b, 0x9b, 0x5b, 0xdb, 0x3b, 0xbb, 0x7b, 0xfb,
    0x07, 0x87, 0x47, 0xc7, 0x27, 0xa7, 0x67, 0xe7,
    0x17, 0x97, 0x57, 0xd7, 0x37, 0xb7, 0x77, 0xf7,
    0x0f, 0x8f, 0x4f, 0xcf, 0x2f, 0xaf, 0x6f, 0xef,
    0x1f, 0x9f, 0x5f, 0xdf, 0x3f, 0xbf, 0x7f, 0xff,
};
unsigned char Reverse_byte(unsigned char c)
{
    return table[c];
}
// 处理64字节为120字节的示例函数（此处简单复制并填充0）
void process_block(const unsigned char* in, size_t in_len, unsigned char* out, size_t out_len) {
    // 实际处理逻辑请替换
    memset(out, 0, out_len);
    size_t copy_len = in_len < out_len ? in_len : out_len;
    memcpy(out, in, copy_len);
    // 例如：可以在此处做解码、插值、格式转换等
    
    unsigned int header = *(unsigned int*)in;
    assert(in_len == 64);
    assert((header & 0xFF) == 0x3c);
    assert(((header >> 24) & 0xFF) == 1);
    g_total++;
    if (((header >> 8) & 0xFF)<=1)
    {
        //audio_dump_data_align_size(ADUMP_DOWNLINK, &p_sco_data->data[0], 60);
        memmove(g_audio_cvsd_env.out_buf, (int16_t*)(g_audio_cvsd_env.out_buf + g_audio_cvsd_env.out_len_interpolate), FIR_FILTER_LENGTH * sizeof(int16_t));
        //memcpy(g_audio_cvsd_env.bit_buf, &p_sco_data->data[0], BT_CVSD_FRAME_LEN);
        for (int i = 0; i < BT_CVSD_FRAME_LEN; i++)
        {
            out[i] = Reverse_byte(out[i+4]);
        }
        cvsdDecode(&(g_audio_cvsd_env.cvsd_d), (const uint8_t*)(&out[0]), BT_CVSD_FRAME_LEN, (int16_t*)(g_audio_cvsd_env.out_buf_shift));
        //decimation_x8(g_audio_cvsd_env.out_buf, g_audio_cvsd_env.out_len_interp_FIR_assumpt, g_audio_cvsd_env.decimate_buf, BT_CVSD_FRAME_LEN);
        decimation_x8(g_audio_cvsd_env.out_buf, g_audio_cvsd_env.out_len_interp_FIR_assumpt, (int16_t*)&out[0], BT_CVSD_FRAME_LEN);
        //audio_dump_data_align_size(ADUMP_DOWNLINK_AGC, &p_sco_data->data[0], 120);

        extern void g711plc_apply_filter(LowcFE_c * lc, short* s, int update);
        if (((header >> 8) & 0xFF) == 1) {
            g_error1++;
            g711plc_apply_filter(&g_plc, (short*)(&out[0]), 1);
        }
        else
            g711plc_apply_filter(&g_plc, (short*)(&out[0]), 0);
        g711plc_addtohistory(&g_plc, (short*)(&out[0]));
    }
    else
    {
        g_error++;
        g711plc_dofe(&g_plc, (short*)(&out[0]));
    }
}
void change_extension(const char* src, const char* new_ext, char* dst, size_t dst_size) {
    const char* dot = strrchr(src, '.');
    size_t base_len = dot ? (size_t)(dot - src) : strlen(src);
    if (base_len + strlen(new_ext) + 1 > dst_size) {
        // 缓冲区不够
        dst[0] = 0;
        return;
    }
    strncpy(dst, src, base_len);
    dst[base_len] = 0;
    strcat(dst, new_ext);
}
void write_wav_header(FILE* f, int sample_rate, int bits_per_sample, int channels, int data_size) {
    int byte_rate = sample_rate * channels * bits_per_sample / 8;
    int block_align = channels * bits_per_sample / 8;
    int chunk_size = 36 + data_size;

    // RIFF header
    fwrite("RIFF", 1, 4, f);
    uint32_t sz = chunk_size;
    fwrite(&sz, 4, 1, f);
    fwrite("WAVE", 1, 4, f);

    // fmt chunk
    fwrite("fmt ", 1, 4, f);
    uint32_t fmt_size = 16;
    fwrite(&fmt_size, 4, 1, f);
    uint16_t audio_format = 1; // PCM
    fwrite(&audio_format, 2, 1, f);
    uint16_t num_channels = channels;
    fwrite(&num_channels, 2, 1, f);
    uint32_t s_rate = sample_rate;
    fwrite(&s_rate, 4, 1, f);
    uint32_t b_rate = byte_rate;
    fwrite(&b_rate, 4, 1, f);
    uint16_t b_align = block_align;
    fwrite(&b_align, 2, 1, f);
    uint16_t bps = bits_per_sample;
    fwrite(&bps, 2, 1, f);

    // data chunk
    fwrite("data", 1, 4, f);
    uint32_t d_size = data_size;
    fwrite(&d_size, 4, 1, f);
}
int main(int argc, char* argv[]) {
    if (argc != 3) {
        printf("Usage: %s input.bin output.pcm\n", argv[0]);
        return 1;
    }
    bt_cvsd_init();
    cvsd_g711plc_construct(&g_plc);

    FILE* fin = fopen(argv[1], "rb");
    if (!fin) {
        perror("Open input file failed");
        return 1;
    }
    FILE* fout = fopen(argv[2], "wb");
    if (!fout) {
        perror("Open output file failed");
        fclose(fin);
        return 1;
    }
    char newname[256];
    change_extension(argv[2], ".wav", newname, sizeof(newname));

    FILE* fout2 = fopen(newname, "wb");
    if (!fout2) {
        perror("Open output file failed");
        fclose(fin);
        fclose(fout);
        return 1;
    }

    unsigned char inbuf[64];
    unsigned char outbuf[120];
    size_t n;

    while ((n = fread(inbuf, 1, 64, fin)) > 0) {
        process_block(inbuf, n, outbuf, 120);
        fwrite(outbuf, 1, 120, fout);
    }
    fclose(fout);
    fout = fopen(argv[2], "rb");
    if (!fout) {
        perror("Open output file failed");
        fclose(fin);
        fclose(fout2);
        return 1;
    }

    // 获取PCM数据大小
    fseek(fout, 0, SEEK_END);
    int data_size = ftell(fout);
    fseek(fout, 0, SEEK_SET);

    // 写WAV头
    write_wav_header(fout2, 8000, 16, 1, data_size);

    // 拷贝PCM数据
    char buf[4096];
    while ((n = fread(buf, 1, sizeof(buf), fout)) > 0) {
        fwrite(buf, 1, n, fout2);
    }

    fclose(fin);
    fclose(fout);
    fclose(fout2);
    printf("total:%d,error:%d, error1:%d, per:%.2f, crc per %.2f\n", g_total, g_error, g_error1, (float)g_error * 100 / g_total, (float)g_error1 * 100 / g_total);
    printf("size %d Done.\n", data_size);
    return 0;
}