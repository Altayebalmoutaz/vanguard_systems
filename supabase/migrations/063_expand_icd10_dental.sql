-- Expand the dental ICD-10-CM reference subset (analytics.icd10_dental_gem_axis).
-- The seeded set came from the 2015 GEM crosswalk and stops at 4-char granularity
-- for several families (e.g. K05.31), so modern billable leaves the model emits
-- (K05.311 chronic periodontitis, localized, slight) failed validation.
--
-- These are current ICD-10-CM codes that post-date the 2015 GEM, so they carry no
-- ICD-9 mapping (icd9_* set to a NoDx sentinel). analytics is the table; public is
-- a view over it, so no separate sync is required. Idempotent: skips codes already
-- present (matched on dotted icd10_code). record_id derived as 'EXP_'||compact.

begin;

insert into analytics.icd10_dental_gem_axis (
  record_id, icd10_code_compact, icd10_code, icd10_description,
  icd9_code_compact, icd9_code, icd9_description,
  axis_group, flag_1, flag_2, flag_3, flag_4, flag_5,
  gem_axis, combined_line, effective_at, notes
)
select
  'EXP_' || v.compact, v.compact, v.dotted, v.descr,
  'NODX', 'NoDx', 'No ICD-9 GEM mapping (post-2015 ICD-10-CM)',
  '', '0', '0', '0', '0', '0',
  '', v.compact || '  ' || v.dotted || '  ' || v.descr, now(),
  'agent_expansion_2026-08 dental ICD-10-CM'
from (values
  -- Dental caries (K02)
  ('K023',  'K02.3',  'Arrested dental caries'),
  ('K0251', 'K02.51', 'Dental caries on pit and fissure surface limited to enamel'),
  ('K0252', 'K02.52', 'Dental caries on pit and fissure surface penetrating into dentin'),
  ('K0253', 'K02.53', 'Dental caries on pit and fissure surface penetrating into pulp'),
  ('K0261', 'K02.61', 'Dental caries on smooth surface limited to enamel'),
  ('K0262', 'K02.62', 'Dental caries on smooth surface penetrating into dentin'),
  ('K0263', 'K02.63', 'Dental caries on smooth surface penetrating into pulp'),
  ('K027',  'K02.7',  'Dental root caries'),
  ('K029',  'K02.9',  'Dental caries, unspecified'),
  -- Other diseases of hard tissues of teeth (K03)
  ('K030',  'K03.0',  'Excessive attrition of teeth'),
  ('K031',  'K03.1',  'Abrasion of teeth'),
  ('K032',  'K03.2',  'Erosion of teeth'),
  ('K033',  'K03.3',  'Pathological resorption of teeth'),
  ('K034',  'K03.4',  'Hypercementosis'),
  ('K035',  'K03.5',  'Ankylosis of teeth'),
  ('K036',  'K03.6',  'Deposits [accretions] on teeth'),
  ('K037',  'K03.7',  'Posteruptive color changes of dental hard tissues'),
  ('K0381', 'K03.81', 'Cracked tooth'),
  ('K0389', 'K03.89', 'Other specified diseases of hard tissues of teeth'),
  ('K039',  'K03.9',  'Disease of hard tissues of teeth, unspecified'),
  -- Diseases of pulp and periapical tissues (K04)
  ('K0401', 'K04.01', 'Reversible pulpitis'),
  ('K0402', 'K04.02', 'Irreversible pulpitis'),
  ('K041',  'K04.1',  'Necrosis of pulp'),
  ('K042',  'K04.2',  'Pulp degeneration'),
  ('K043',  'K04.3',  'Abnormal hard tissue formation in pulp'),
  ('K044',  'K04.4',  'Acute apical periodontitis of pulpal origin'),
  ('K045',  'K04.5',  'Chronic apical periodontitis'),
  ('K046',  'K04.6',  'Periapical abscess with sinus'),
  ('K047',  'K04.7',  'Periapical abscess without sinus'),
  ('K048',  'K04.8',  'Radicular cyst'),
  ('K0490', 'K04.90', 'Unspecified diseases of pulp and periapical tissues'),
  ('K0499', 'K04.99', 'Other diseases of pulp and periapical tissues'),
  -- Gingivitis and periodontal diseases (K05)
  ('K0500', 'K05.00', 'Acute gingivitis, plaque induced'),
  ('K0501', 'K05.01', 'Acute gingivitis, non-plaque induced'),
  ('K0510', 'K05.10', 'Chronic gingivitis, plaque induced'),
  ('K0511', 'K05.11', 'Chronic gingivitis, non-plaque induced'),
  ('K05211','K05.211','Aggressive periodontitis, localized, slight'),
  ('K05212','K05.212','Aggressive periodontitis, localized, moderate'),
  ('K05213','K05.213','Aggressive periodontitis, localized, severe'),
  ('K05219','K05.219','Aggressive periodontitis, localized, unspecified severity'),
  ('K05221','K05.221','Aggressive periodontitis, generalized, slight'),
  ('K05222','K05.222','Aggressive periodontitis, generalized, moderate'),
  ('K05223','K05.223','Aggressive periodontitis, generalized, severe'),
  ('K05229','K05.229','Aggressive periodontitis, generalized, unspecified severity'),
  ('K05311','K05.311','Chronic periodontitis, localized, slight'),
  ('K05312','K05.312','Chronic periodontitis, localized, moderate'),
  ('K05313','K05.313','Chronic periodontitis, localized, severe'),
  ('K05319','K05.319','Chronic periodontitis, localized, unspecified severity'),
  ('K05321','K05.321','Chronic periodontitis, generalized, slight'),
  ('K05322','K05.322','Chronic periodontitis, generalized, moderate'),
  ('K05323','K05.323','Chronic periodontitis, generalized, severe'),
  ('K05329','K05.329','Chronic periodontitis, generalized, unspecified severity'),
  ('K054',  'K05.4',  'Periodontosis'),
  ('K055',  'K05.5',  'Other periodontal diseases'),
  ('K056',  'K05.6',  'Periodontal disease, unspecified'),
  -- Gingiva and edentulous alveolar ridge (K06)
  ('K06010','K06.010','Localized gingival recession, unspecified'),
  ('K06011','K06.011','Localized gingival recession, minimal'),
  ('K06012','K06.012','Localized gingival recession, moderate'),
  ('K06013','K06.013','Localized gingival recession, severe'),
  ('K06020','K06.020','Generalized gingival recession, unspecified'),
  ('K06021','K06.021','Generalized gingival recession, minimal'),
  ('K06022','K06.022','Generalized gingival recession, moderate'),
  ('K06023','K06.023','Generalized gingival recession, severe'),
  ('K061',  'K06.1',  'Gingival enlargement'),
  ('K062',  'K06.2',  'Gingival and edentulous alveolar ridge lesions associated with trauma'),
  ('K068',  'K06.8',  'Other specified disorders of gingiva and edentulous alveolar ridge'),
  ('K069',  'K06.9',  'Disorder of gingiva and edentulous alveolar ridge, unspecified'),
  -- Disorders of tooth development and eruption (K00)
  ('K004',  'K00.4',  'Disturbances in tooth formation'),
  ('K005',  'K00.5',  'Hereditary disturbances in tooth structure, not elsewhere classified'),
  ('K006',  'K00.6',  'Disturbances in tooth eruption'),
  ('K007',  'K00.7',  'Teething syndrome'),
  ('K008',  'K00.8',  'Other disorders of tooth development'),
  ('K009',  'K00.9',  'Disorder of tooth development, unspecified'),
  -- Embedded and impacted teeth (K01)
  ('K010',  'K01.0',  'Embedded teeth'),
  ('K011',  'K01.1',  'Impacted teeth'),
  -- Other disorders of teeth and supporting structures (K08)
  ('K083',  'K08.3',  'Retained dental root'),
  ('K08109','K08.109','Complete loss of teeth, unspecified cause, unspecified class'),
  ('K08409','K08.409','Partial loss of teeth, unspecified cause, unspecified class'),
  ('K0851', 'K08.51', 'Open restoration margins of tooth'),
  ('K08530','K08.530','Fractured dental restorative material without loss of material'),
  ('K08531','K08.531','Fractured dental restorative material with loss of material'),
  ('K0854', 'K08.54', 'Contour of existing restoration of tooth biologically incompatible with oral health'),
  ('K0889', 'K08.89', 'Other specified disorders of teeth and supporting structures'),
  ('K089',  'K08.9',  'Disorder of teeth and supporting structures, unspecified'),
  -- Salivary glands and oral mucosa (K11-K14)
  ('K117',  'K11.7',  'Disturbances of salivary secretion'),
  ('K120',  'K12.0',  'Recurrent oral aphthae'),
  ('K121',  'K12.1',  'Other forms of stomatitis'),
  ('K1230', 'K12.30', 'Oral mucositis (ulcerative), unspecified'),
  ('K130',  'K13.0',  'Diseases of lips'),
  ('K1379', 'K13.79', 'Other lesions of oral mucosa'),
  ('K140',  'K14.0',  'Glossitis'),
  ('K146',  'K14.6',  'Glossodynia'),
  -- Diseases of jaws / endodontic sequelae (M27)
  ('M2740', 'M27.40', 'Unspecified cyst of jaw'),
  ('M2751', 'M27.51', 'Perforation of root canal space due to endodontic treatment'),
  ('M2752', 'M27.52', 'Endodontic overfill'),
  ('M2753', 'M27.53', 'Endodontic underfill'),
  ('M2759', 'M27.59', 'Other periradicular pathology associated with previous endodontic treatment'),
  -- Temporomandibular joint disorders (M26.6)
  ('M26601','M26.601','Right temporomandibular joint disorder, unspecified'),
  ('M26602','M26.602','Left temporomandibular joint disorder, unspecified'),
  ('M26603','M26.603','Bilateral temporomandibular joint disorder, unspecified'),
  ('M26609','M26.609','Unspecified temporomandibular joint disorder, unspecified side'),
  ('M26621','M26.621','Arthralgia of right temporomandibular joint'),
  ('M26622','M26.622','Arthralgia of left temporomandibular joint'),
  ('M26623','M26.623','Arthralgia of bilateral temporomandibular joint'),
  ('M26629','M26.629','Arthralgia of temporomandibular joint, unspecified side'),
  -- Malocclusion (M26.2 / M26.4 / M26.9)
  ('M26211','M26.211','Malocclusion, Angle''s class I'),
  ('M26212','M26.212','Malocclusion, Angle''s class II'),
  ('M26213','M26.213','Malocclusion, Angle''s class III'),
  ('M26220','M26.220','Open anterior occlusal relationship'),
  ('M26221','M26.221','Open posterior occlusal relationship'),
  ('M264',  'M26.4',  'Malocclusion, unspecified'),
  ('M269',  'M26.9',  'Dentofacial anomaly, unspecified'),
  -- Traumatic tooth fracture (S02.5-, 7th char)
  ('S025XXA','S02.5XXA','Fracture of tooth (traumatic), initial encounter for closed fracture'),
  ('S025XXD','S02.5XXD','Fracture of tooth (traumatic), subsequent encounter for fracture with routine healing'),
  ('S025XXS','S02.5XXS','Fracture of tooth (traumatic), sequela'),
  -- Encounters and status (Z)
  ('Z0120', 'Z01.20', 'Encounter for dental examination and cleaning without abnormal findings'),
  ('Z0121', 'Z01.21', 'Encounter for dental examination and cleaning with abnormal findings'),
  ('Z1384', 'Z13.84', 'Encounter for screening for dental disorders'),
  ('Z463',  'Z46.3',  'Encounter for fitting and adjustment of dental prosthetic device'),
  ('Z98810','Z98.810','Dental sealant status'),
  ('Z98811','Z98.811','Dental restoration status')
) as v(compact, dotted, descr)
where not exists (
  select 1 from analytics.icd10_dental_gem_axis x where x.icd10_code = v.dotted
);

commit;
