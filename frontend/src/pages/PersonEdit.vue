<template>
  <div>
    <a-card>
      <template #title>
        <a-space>
          <a-button size="small" @click="$router.back()">← 返回</a-button>
          <span>{{ isEdit ? `人物详情：${form.name || ''}` : '新增人物' }}</span>
        </a-space>
      </template>

      <a-row :gutter="24">
        <a-col :span="16">
          <a-form layout="vertical" :model="form">
            <a-row :gutter="16">
              <a-col :span="8">
                <a-form-item label="姓名" required>
                  <a-input v-model:value="form.name" placeholder="姓名" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="性别">
                  <a-radio-group v-model:value="form.gender">
                    <a-radio value="male">男</a-radio>
                    <a-radio value="female">女</a-radio>
                    <a-radio value="unknown">未知</a-radio>
                  </a-radio-group>
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="代数（第几代）">
                  <a-input-number v-model:value="form.generation" :min="1" :max="50" style="width: 100%" placeholder="可选" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="出生年份">
                  <a-input-number v-model:value="form.birth_year" :min="1000" :max="2100" style="width: 100%" placeholder="如 1850" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="出生日期">
                  <a-input v-model:value="form.birth_date" placeholder="如 1850-03-12（可选）" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="出生地">
                  <a-input v-model:value="form.birth_place" placeholder="出生地" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="逝世年份">
                  <a-input-number v-model:value="form.death_year" :min="1000" :max="2100" style="width: 100%" placeholder="如 1920" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="逝世日期">
                  <a-input v-model:value="form.death_date" placeholder="如 1920-08-01（可选）" />
                </a-form-item>
              </a-col>
              <a-col :span="8">
                <a-form-item label="逝地">
                  <a-input v-model:value="form.death_place" placeholder="逝世地" />
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="在世">
                  <a-switch v-model:checked="alive" checked-children="在世" un-checked-children="已故" />
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="简介/生平">
                  <a-textarea v-model:value="form.biography" :rows="4" placeholder="生平简介" />
                </a-form-item>
              </a-col>
              <a-col :span="24">
                <a-form-item label="备注">
                  <a-textarea v-model:value="form.notes" :rows="2" placeholder="备注" />
                </a-form-item>
              </a-col>
            </a-row>
            <a-space>
              <a-button type="primary" :loading="saving" @click="save">保存</a-button>
              <a-button @click="$router.back()">取消</a-button>
            </a-space>
          </a-form>
        </a-col>

        <a-col :span="8">
          <a-card size="small" title="照片">
            <div class="photo-box">
              <img v-if="form.photo_url" :src="form.photo_url" class="photo" />
              <div v-else class="photo-empty">暂无照片</div>
            </div>
            <a-upload
              :show-upload-list="false"
              :before-upload="uploadPhoto"
              accept="image/*"
            >
              <a-button size="small" :loading="photoLoading">上传照片</a-button>
            </a-upload>
          </a-card>

          <a-card v-if="isEdit" size="small" title="亲属关系" style="margin-top: 12px">
            <a-list size="small" :data-source="relations">
              <template #renderItem="{ item }">
                <a-list-item>
                  <span>
                    <a-tag :color="item.type === 'PARENT_OF' ? 'blue' : 'magenta'">
                      {{ item.type === 'PARENT_OF' ? '亲缘' : '配偶' }}
                    </a-tag>
                    {{ item.from_person_id === form.person_id ? item.to_name : item.from_name }}
                    （{{ item.type === 'PARENT_OF'
                      ? (item.from_person_id === form.person_id ? '子女' : '父母')
                      : '配偶' }}）
                  </span>
                  <a-popconfirm title="删除该关系？" @confirm="removeRelation(item.rel_id)">
                    <a style="color: #ff4d4f">删</a>
                  </a-popconfirm>
                </a-list-item>
              </template>
            </a-list>
            <div style="margin-top: 8px">
              <a-form layout="inline" size="small">
                <a-form-item>
                  <a-select v-model:value="newRelType" style="width: 110px">
                    <a-select-option value="parent_child">亲缘</a-select-option>
                    <a-select-option value="spouse">配偶</a-select-option>
                  </a-select>
                </a-form-item>
                <a-form-item>
                  <a-select
                    v-model:value="newRelPerson"
                    show-search
                    placeholder="选择关联人物"
                    style="width: 160px"
                    :filter-option="filterOption"
                  >
                    <a-select-option v-for="p in allPersons" :key="p.person_id" :value="p.person_id">
                      {{ p.name }}
                    </a-select-option>
                  </a-select>
                </a-form-item>
                <a-form-item>
                  <a-button type="link" size="small" @click="addRelation">添加</a-button>
                </a-form-item>
              </a-form>
              <div class="hint">
                「亲缘」关系为 父母→子女，先选择的方向视类型而定：选择【子女】则当前人物为父母。
              </div>
            </div>
          </a-card>
        </a-col>
      </a-row>
    </a-card>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { message } from 'ant-design-vue'
import {
  createPersonApi,
  createRelationApi,
  deleteRelationApi,
  getPersonApi,
  listLineagesApi,
  listPersonsApi,
  listRelationsApi,
  updatePersonApi,
  uploadPhotoApi,
} from '@/api'
import { useAuthStore } from '@/stores/auth'
import type { Lineage, Person, Relation } from '@/types'

const route = useRoute()
const router = useRouter()
const auth = useAuthStore()

const isEdit = computed(() => !!route.params.id)
const personId = computed(() => route.params.id as string)

const form = reactive<Partial<Person>>({
  name: '',
  gender: 'unknown',
  generation: null,
  birth_year: null,
  birth_date: null,
  birth_place: null,
  death_year: null,
  death_date: null,
  death_place: null,
  biography: null,
  notes: null,
  photo_url: null,
  is_alive: null,
  lineage_id: null,
  branch_id: null,
})

const lineages = ref<Lineage[]>([])
const currentFormBranches = computed(() => {
  const l = lineages.value.find((x) => x.lineage_id === form.lineage_id)
  return l?.branches || []
})
const onFormLineageChange = () => {
  form.branch_id = null
}

const alive = ref(true)
const saving = ref(false)
const photoLoading = ref(false)

const relations = ref<Relation[]>([])
const allPersons = ref<Person[]>([])
const newRelType = ref<'parent_child' | 'spouse'>('parent_child')
const newRelPerson = ref<string>()

const load = async () => {
  if (!isEdit.value) return
  const p = await getPersonApi(personId.value)
  Object.assign(form, p)
  alive.value = p.is_alive !== false
  relations.value = (await listRelationsApi()).filter(
    (r) => r.from_person_id === personId.value || r.to_person_id === personId.value,
  )
  const data = await listPersonsApi({ limit: 5000 })
  allPersons.value = data.items.filter((x) => x.person_id !== personId.value)
}

const save = async () => {
  if (!form.name?.trim()) {
    message.warning('请填写姓名')
    return
  }
  saving.value = true
  try {
    const payload = { ...form, is_alive: alive.value }
    if (isEdit.value) {
      await updatePersonApi(personId.value, payload)
      message.success('已保存')
    } else {
      const created = await createPersonApi(payload)
      message.success('创建成功')
      router.replace(`/persons/${created.person_id}`)
    }
  } finally {
    saving.value = false
  }
}

const uploadPhoto = async (file: File) => {
  photoLoading.value = true
  try {
    const updated = await uploadPhotoApi(personId.value, file)
    form.photo_url = updated.photo_url
    message.success('照片已上传')
  } finally {
    photoLoading.value = false
  }
  return false
}

const filterOption = (input: string, option: any) =>
  (option?.value as string).toLowerCase().includes(input.toLowerCase()) ||
  (option?.label as string)?.toLowerCase().includes(input.toLowerCase())

const addRelation = async () => {
  if (!newRelPerson.value) {
    message.warning('请选择关联人物')
    return
  }
  // 亲缘关系：父母 → 子女；若用户选择的是子女，则当前人为父/母
  let fromId = personId.value
  let toId = newRelPerson.value
  await createRelationApi({ type: newRelType.value, from_person_id: fromId, to_person_id: toId })
  message.success('已添加关系')
  newRelPerson.value = undefined
  load()
}

const removeRelation = async (relId: string) => {
  await deleteRelationApi(relId)
  message.success('已删除')
  load()
}

onMounted(async () => {
  load()
  try {
    lineages.value = await listLineagesApi()
  } catch {
    /* 忽略 */
  }
})
</script>

<style scoped>
.photo-box {
  height: 180px;
  display: flex;
  align-items: center;
  justify-content: center;
  border: 1px dashed #d9d9d9;
  border-radius: 6px;
  margin-bottom: 10px;
  overflow: hidden;
  background: #fafafa;
}
.photo {
  max-width: 100%;
  max-height: 180px;
}
.photo-empty {
  color: #bbb;
}
.hint {
  font-size: 12px;
  color: #999;
  margin-top: 4px;
}
</style>
