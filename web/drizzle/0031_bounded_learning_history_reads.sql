CREATE INDEX IF NOT EXISTS `learning_records_resource_identity_time_idx`
ON `learning_records` (
  `resource`,
  json_extract(`payload`, '$.model_identity'),
  `sort_epoch`,
  `record_key`
);
--> statement-breakpoint
CREATE TABLE IF NOT EXISTS `learning_record_counts` (
  `resource` text NOT NULL,
  `model_identity` text NOT NULL,
  `record_count` integer NOT NULL,
  PRIMARY KEY (`resource`, `model_identity`),
  CHECK (`record_count` >= 0)
);
--> statement-breakpoint
INSERT INTO `learning_record_counts` (`resource`,`model_identity`,`record_count`)
SELECT `resource`,'',count(*) FROM `learning_records` GROUP BY `resource`
ON CONFLICT (`resource`,`model_identity`) DO UPDATE SET
  `record_count`=excluded.`record_count`;
--> statement-breakpoint
INSERT INTO `learning_record_counts` (`resource`,`model_identity`,`record_count`)
SELECT `resource`,json_extract(`payload`, '$.model_identity'),count(*)
FROM `learning_records`
WHERE json_type(`payload`, '$.model_identity')='text'
  AND length(json_extract(`payload`, '$.model_identity'))>0
GROUP BY `resource`,json_extract(`payload`, '$.model_identity')
ON CONFLICT (`resource`,`model_identity`) DO UPDATE SET
  `record_count`=excluded.`record_count`;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `learning_record_count_insert`
AFTER INSERT ON `learning_records`
BEGIN
  INSERT INTO `learning_record_counts` (`resource`,`model_identity`,`record_count`)
  VALUES (NEW.`resource`,'',1)
  ON CONFLICT (`resource`,`model_identity`) DO UPDATE SET
    `record_count`=`record_count`+1;
  INSERT INTO `learning_record_counts` (`resource`,`model_identity`,`record_count`)
  SELECT NEW.`resource`,json_extract(NEW.`payload`, '$.model_identity'),1
  WHERE json_type(NEW.`payload`, '$.model_identity')='text'
    AND length(json_extract(NEW.`payload`, '$.model_identity'))>0
  ON CONFLICT (`resource`,`model_identity`) DO UPDATE SET
    `record_count`=`record_count`+1;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `learning_record_count_delete`
AFTER DELETE ON `learning_records`
BEGIN
  UPDATE `learning_record_counts` SET `record_count`=`record_count`-1
  WHERE `resource`=OLD.`resource` AND `model_identity`='';
  UPDATE `learning_record_counts` SET `record_count`=`record_count`-1
  WHERE `resource`=OLD.`resource`
    AND `model_identity`=json_extract(OLD.`payload`, '$.model_identity');
  DELETE FROM `learning_record_counts` WHERE `record_count`=0;
END;
--> statement-breakpoint
CREATE TRIGGER IF NOT EXISTS `learning_record_count_identity_update`
AFTER UPDATE OF `payload` ON `learning_records`
WHEN coalesce(json_extract(OLD.`payload`, '$.model_identity'),'')
  IS NOT coalesce(json_extract(NEW.`payload`, '$.model_identity'),'')
BEGIN
  UPDATE `learning_record_counts` SET `record_count`=`record_count`-1
  WHERE `resource`=OLD.`resource`
    AND `model_identity`=json_extract(OLD.`payload`, '$.model_identity');
  DELETE FROM `learning_record_counts` WHERE `record_count`=0;
  INSERT INTO `learning_record_counts` (`resource`,`model_identity`,`record_count`)
  SELECT NEW.`resource`,json_extract(NEW.`payload`, '$.model_identity'),1
  WHERE json_type(NEW.`payload`, '$.model_identity')='text'
    AND length(json_extract(NEW.`payload`, '$.model_identity'))>0
  ON CONFLICT (`resource`,`model_identity`) DO UPDATE SET
    `record_count`=`record_count`+1;
END;
