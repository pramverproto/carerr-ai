import React, { useState, useEffect, useRef, useCallback } from 'react';
import {
  Form, Input, Button, Select, InputNumber, Collapse, Steps,
  Slider, Alert, Divider, App,
} from 'antd';
import { PlusOutlined, MinusCircleOutlined, UploadOutlined } from '@ant-design/icons';
import { useNavigate } from 'react-router-dom';
import { useAppStore } from '@/store/appStore';
import { api } from '@/api/client';
import type { AssessRequest } from '@/types';

const { TextArea } = Input;
const { Panel } = Collapse;

const ASSESS_STEPS = [
  '解析候选人信息',
  '评估技能画像',
  '评估知识储备',
  '评估认知能力',
  '评估工作特质与兴趣',
  '生成综合摘要',
];

const EDUCATION_OPTIONS = [
  { value: '大专', label: '大专' },
  { value: '本科', label: '本科' },
  { value: '硕士', label: '硕士' },
  { value: '博士', label: '博士' },
  { value: '其他', label: '其他' },
];

const BIG_FIVE_DIMS = [
  { key: 'O', label: '开放性 (Openness)' },
  { key: 'C', label: '尽责性 (Conscientiousness)' },
  { key: 'E', label: '外向性 (Extraversion)' },
  { key: 'A', label: '宜人性 (Agreeableness)' },
  { key: 'ES', label: '情绪稳定性 (Emotional Stability)' },
];

const RIASEC_DIMS = [
  { key: 'R', label: '现实型 (Realistic)' },
  { key: 'I', label: '研究型 (Investigative)' },
  { key: 'A', label: '艺术型 (Artistic)' },
  { key: 'S', label: '社会型 (Social)' },
  { key: 'E', label: '企业型 (Enterprising)' },
  { key: 'C', label: '常规型 (Conventional)' },
];

const Profile: React.FC = () => {
  const [form] = Form.useForm();
  const { message } = App.useApp();
  const navigate = useNavigate();
  const { assessStatus, assessError, profileDraft, setProfileDraft, setAssessStatus, setAssessmentId, setReportData, resetDownstream } = useAppStore();

  const [stepIndex, setStepIndex] = useState(0);
  const saveTimerRef = useRef<ReturnType<typeof setTimeout>>();

  /** 表单变更时自动保存草稿（防抖 1 秒） */
  const handleValuesChange = useCallback(() => {
    clearTimeout(saveTimerRef.current);
    saveTimerRef.current = setTimeout(() => {
      const values = form.getFieldsValue(true);
      setProfileDraft({
        resume: {
          candidate: {
            name: values.name,
            age: values.age,
            education: values.education,
            current_title: values.current_title,
            years_of_experience: values.years_of_experience,
          },
          experiences: values.experiences,
          skills: values.skills,
          certifications: values.certifications,
        },
        supplement: values.supplement,
      });
    }, 1000);
  }, [form, setProfileDraft]);

  // 恢复草稿
  useEffect(() => {
    if (profileDraft) {
      const { resume, supplement, bigfive, riasec } = profileDraft;
      if (resume?.candidate) {
        form.setFieldsValue({
          ...resume.candidate,
          experiences: (resume.experiences ?? []).length > 0 ? resume.experiences : [{}],
          skills: resume.skills ?? [],
          certifications: resume.certifications ?? [],
          supplement,
          bigfive,
          riasec,
        });
      }
    }
  }, []);

  // ── 简历上传 ────────────────────────────────────────────────────
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploadingResume, setUploadingResume] = useState(false);

  const handleResumeFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    // 清空 input 以便用户可以重复上传同一文件
    if (e.target) e.target.value = '';
    if (!file) return;

    const maxBytes = 10 * 1024 * 1024;
    if (file.size > maxBytes) {
      message.error('文件过大，请上传 10MB 以内的文件');
      return;
    }
    const isDocx = file.type === 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' || file.name.endsWith('.docx');
    const okType = file.type.startsWith('image/') || file.type === 'application/pdf' || isDocx;
    if (!okType) {
      message.error('仅支持图片（PNG/JPG）、PDF 或 Word（.docx）格式的简历');
      return;
    }

    setUploadingResume(true);
    const hide = message.loading('AI 正在识别简历内容，请稍候（约 30-60 秒）…', 0);
    try {
      const res = await api.uploadResume(file);
      const data = res.data.extracted;

      // 将提取结果映射到表单字段
      const formValues = {
        name: data.name ?? undefined,
        age: data.age ?? undefined,
        education: data.education ?? undefined,
        current_title: data.current_title ?? undefined,
        years_of_experience: data.years_of_experience ?? undefined,
        experiences:
          (data.experiences ?? []).length > 0
            ? data.experiences
            : [{}],
        skills: data.skills ?? [],
        certifications: data.certifications ?? [],
        supplement: data.supplement ?? undefined,
      };
      form.setFieldsValue(formValues);

      // 同步保存到 store，切换页面后不丢失
      setProfileDraft({
        resume: {
          candidate: {
            name: formValues.name ?? '',
            age: formValues.age,
            education: formValues.education,
            current_title: formValues.current_title,
            years_of_experience: formValues.years_of_experience,
          },
          experiences: (formValues.experiences ?? []) as import('../types').ResumeExperience[],
          skills: formValues.skills,
          certifications: formValues.certifications,
        },
        supplement: formValues.supplement as string,
      });

      hide();
      message.success('简历信息已自动填充，请核对后再提交');
    } catch (err: unknown) {
      hide();
      const msg = (err as { response?: { data?: { detail?: string } }; message?: string })
        ?.response?.data?.detail || (err as { message?: string })?.message || '简历识别失败，请重试';
      message.error(msg);
    } finally {
      setUploadingResume(false);
    }
  };

  const triggerResumeUpload = () => fileInputRef.current?.click();

  // 自动步进
  useEffect(() => {
    if (assessStatus !== 'loading') {
      setStepIndex(0);
      return;
    }
    const timer = setInterval(() => {
      setStepIndex((s) => Math.min(s + 1, ASSESS_STEPS.length - 1));
    }, 25_000);
    return () => clearInterval(timer);
  }, [assessStatus]);

  const isLoading = assessStatus === 'loading';

  const handleSubmit = async (values: Record<string, unknown>) => {
    // 构建请求体
    const experiences = (values.experiences as Record<string, unknown>[] | undefined) || [];
    const skills = (values.skills as string[] | undefined) || [];
    const certifications = (values.certifications as string[] | undefined) || [];

    const requestBody: AssessRequest = {
      resume: {
        candidate: {
          name: values.name as string,
          age: values.age as number | undefined,
          education: values.education as string | undefined,
          current_title: values.current_title as string | undefined,
          years_of_experience: values.years_of_experience as number | undefined,
        },
        experiences: experiences.map((exp) => ({
          company: exp.company as string,
          title: exp.title as string,
          duration: exp.duration as string,
          responsibilities: ((exp.responsibilities as string) || '')
            .split('\n')
            .map((s: string) => s.trim())
            .filter(Boolean),
        })),
        skills,
        certifications: certifications.length ? certifications : undefined,
      },
      supplement: values.supplement as string,
    };

    // Big Five
    if (values.bigfive_O !== undefined) {
      requestBody.bigfive = {
        O: values.bigfive_O as number,
        C: values.bigfive_C as number,
        E: values.bigfive_E as number,
        A: values.bigfive_A as number,
        ES: values.bigfive_ES as number,
      };
    }

    // RIASEC
    if (values.riasec_R !== undefined) {
      requestBody.riasec = {
        R: values.riasec_R as number,
        I: values.riasec_I as number,
        A: values.riasec_A as number,
        S: values.riasec_S as number,
        E: values.riasec_E as number,
        C: values.riasec_C as number,
      };
    }

    // 保存草稿
    setProfileDraft(requestBody);
    setAssessStatus('loading');
    setStepIndex(0);

    try {
      const res = await api.assess(requestBody);
      const { assessment_id } = res.data;
      resetDownstream();
      setAssessmentId(assessment_id);
      setAssessStatus('done');
      message.success('评估完成，正在加载报告...');
      navigate('/assessment');
    } catch (err: unknown) {
      const errMsg = (err as { response?: { data?: { detail?: string } }; message?: string })
        ?.response?.data?.detail || (err as { message?: string })?.message || '评估失败，请重试';
      setAssessStatus('error', errMsg);
    }
  };

  return (
    <div className="max-w-3xl mx-auto">
      <div className="mb-6 flex items-start justify-between gap-4">
        <div>
          <h2 className="text-2xl font-bold text-gray-900 dark:text-gray-100">个人信息完善</h2>
          <p className="text-gray-500 dark:text-gray-400 mt-1">请填写您的基本信息，用于生成专属能力评估报告</p>
        </div>
        {!isLoading && (
          <div className="flex items-center gap-2 flex-shrink-0">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*,application/pdf,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
              className="hidden"
              onChange={handleResumeFileChange}
            />
            <Button
              type="primary"
              icon={<UploadOutlined />}
              loading={uploadingResume}
              onClick={triggerResumeUpload}
            >
              {uploadingResume ? '识别中…' : '上传简历自动填充'}
            </Button>
          </div>
        )}
      </div>

      {assessStatus === 'error' && assessError && (
        <Alert
          type="error"
          message="评估失败"
          description={assessError}
          showIcon
          className="mb-4"
          closable
        />
      )}

      {isLoading ? (
        <div className="bg-blue-50 dark:bg-blue-900/20 rounded-xl p-8 text-center">
          <p className="text-blue-700 dark:text-blue-300 font-medium text-lg mb-6">正在为您生成能力评估报告（约2-3分钟）...</p>
          <Steps
            current={stepIndex}
            direction="vertical"
            size="small"
            items={ASSESS_STEPS.map((title) => ({ title }))}
          />
        </div>
      ) : (
        <Form
          form={form}
          layout="vertical"
          onFinish={handleSubmit}
          onValuesChange={handleValuesChange}
          initialValues={{ experiences: [{}] }}
        >
          <Collapse defaultActiveKey={['basic', 'exp', 'skills', 'supplement']} className="mb-6">
            {/* 基本信息 */}
            <Panel header="基本信息" key="basic">
              <div className="grid grid-cols-2 gap-4">
                <Form.Item name="name" label="姓名" rules={[{ required: true, message: '请输入姓名' }]}>
                  <Input placeholder="请输入姓名" />
                </Form.Item>
                <Form.Item name="age" label="年龄">
                  <InputNumber className="w-full" min={16} max={65} placeholder="年龄" />
                </Form.Item>
                <Form.Item name="education" label="最高学历">
                  <Select options={EDUCATION_OPTIONS} placeholder="请选择学历" />
                </Form.Item>
                <Form.Item name="current_title" label="当前职位">
                  <Input placeholder="如：高级产品经理" />
                </Form.Item>
                <Form.Item name="years_of_experience" label="工作年限">
                  <InputNumber className="w-full" min={0} max={50} placeholder="年" />
                </Form.Item>
              </div>
            </Panel>

            {/* 工作经历 */}
            <Panel header="工作经历" key="exp">
              <Form.List name="experiences">
                {(fields, { add, remove }) => (
                  <>
                    {fields.map(({ key, name }) => (
                      <div key={key} className="border border-gray-200 dark:border-gray-600 rounded-lg p-4 mb-4 relative">
                        <button
                          type="button"
                          onClick={() => remove(name)}
                          className="absolute top-3 right-3 text-gray-400 dark:text-gray-500 hover:text-red-500"
                        >
                          <MinusCircleOutlined />
                        </button>
                        <div className="grid grid-cols-2 gap-4">
                          <Form.Item name={[name, 'company']} label="公司名称"
                            rules={[{ required: true, message: '请输入公司名称' }]}>
                            <Input placeholder="公司全称" />
                          </Form.Item>
                          <Form.Item name={[name, 'title']} label="职位"
                            rules={[{ required: true, message: '请输入职位' }]}>
                            <Input placeholder="如：数据分析师" />
                          </Form.Item>
                          <Form.Item name={[name, 'duration']} label="在职时间">
                            <Input placeholder="如：2021.07 - 2024.03" />
                          </Form.Item>
                        </div>
                        <Form.Item name={[name, 'responsibilities']} label="主要职责（每行一条）">
                          <TextArea
                            rows={4}
                            placeholder={"负责用户增长数据分析，搭建北极星指标体系\n主导A/B实验平台建设，提升实验效率30%"}
                          />
                        </Form.Item>
                      </div>
                    ))}
                    <Button
                      type="dashed"
                      onClick={() => add()}
                      icon={<PlusOutlined />}
                      className="w-full"
                    >
                      添加工作经历
                    </Button>
                  </>
                )}
              </Form.List>
            </Panel>

            {/* 技能与证书 */}
            <Panel header="技能与证书" key="skills">
              <Form.Item name="skills" label="技能（回车添加）">
                <Select
                  mode="tags"
                  placeholder="如：Python、SQL、Tableau、A/B Testing"
                  tokenSeparators={[',']}
                />
              </Form.Item>
              <Form.Item name="certifications" label="证书（可选）">
                <Select
                  mode="tags"
                  placeholder="如：PMP、CFA、AWS认证"
                  tokenSeparators={[',']}
                />
              </Form.Item>
            </Panel>

            {/* 个人补充（必填） */}
            <Panel header={<span>个人补充 <span className="text-red-500">*</span></span>} key="supplement">
              <Form.Item
                name="supplement"
                rules={[{ required: true, message: '请填写个人补充，这是评估质量的关键' }]}
                help="请描述您的职业动机、工作偏好、价值观以及典型工作事件。越详细评估越准确。"
              >
                <TextArea
                  rows={6}
                  maxLength={1000}
                  showCount
                  placeholder={"示例：我对数据驱动决策有强烈热情，擅长从复杂数据中发现业务洞察。\n我倾向于在有自主空间的环境中工作，希望工作成果能直接影响产品方向。\n典型事件：主导了用户分层补贴模型，将ROI从1.12提升至1.46，节省约920万元/季度。"}
                />
              </Form.Item>
            </Panel>

            {/* 心理测评（可选） */}
            <Panel header="心理测评（可选，提升评估精度）" key="bigfive">
              <p className="text-gray-500 dark:text-gray-400 text-sm mb-4">若您已完成大五人格测试，请填写各维度得分（0-100）</p>
              <div className="space-y-3 mb-6">
                {BIG_FIVE_DIMS.map(({ key, label }) => (
                  <div key={key} className="flex items-center gap-4">
                    <span className="text-sm text-gray-600 dark:text-gray-300 w-48">{label}</span>
                    <Form.Item name={`bigfive_${key}`} noStyle>
                      <Slider className="flex-1" min={0} max={100} />
                    </Form.Item>
                    <Form.Item name={`bigfive_${key}`} noStyle>
                      <InputNumber min={0} max={100} className="w-16" />
                    </Form.Item>
                  </div>
                ))}
              </div>

              <Divider />

              <p className="text-gray-500 dark:text-gray-400 text-sm mb-4">若您已完成 RIASEC 职业兴趣测试，请填写各类型得分（0-100）</p>
              <div className="space-y-3">
                {RIASEC_DIMS.map(({ key, label }) => (
                  <div key={key} className="flex items-center gap-4">
                    <span className="text-sm text-gray-600 dark:text-gray-300 w-48">{label}</span>
                    <Form.Item name={`riasec_${key}`} noStyle>
                      <Slider className="flex-1" min={0} max={100} />
                    </Form.Item>
                    <Form.Item name={`riasec_${key}`} noStyle>
                      <InputNumber min={0} max={100} className="w-16" />
                    </Form.Item>
                  </div>
                ))}
              </div>
            </Panel>
          </Collapse>

          <div className="flex justify-end">
            <Button
              type="primary"
              htmlType="submit"
              size="large"
              className="px-8"
            >
              开始评估
            </Button>
          </div>
        </Form>
      )}
    </div>
  );
};

export default Profile;
